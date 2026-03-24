package auth

import (
    "encoding/json"
    "fmt"
    "os"
    "time"

    fyersgosdk "github.com/FyersDev/fyers-go-sdk"
    "github.com/karanshergill/algotrix-go/internal/config"
)

type Token struct {
    AccessToken  string    `json:"access_token"`
    RefreshToken string    `json:"refresh_token"`
	CreatedAt    time.Time `json:"created_at"`
}

type Auth struct {
    cfg    config.FyersConfig
    client *fyersgosdk.Client
    token  *Token
}

func New(cfg config.FyersConfig) *Auth {
    client := fyersgosdk.SetClientData(cfg.AppID, cfg.SecretKey, cfg.RedirectURL)
    return &Auth{
        cfg:    cfg,
        client: client,
    }
}

	func (a *Auth) LoginURL() string {
		return a.client.GetLoginURL()
	}
	
	func (a *Auth) Exchange(authCode string) error {
		resp, err := a.client.GenerateAccessToken(authCode, a.client)
		if err != nil {
			return fmt.Errorf("exchange auth code: %w", err)
		}
		return a.parseAndSave(resp)
	}
	
	func (a *Auth) LoadToken() error {
		data, err := os.ReadFile(a.cfg.TokenPath)
		if err != nil {
			return fmt.Errorf("no saved token: %w", err)
		}
	
		var token Token
		if err := json.Unmarshal(data, &token); err != nil {
			return fmt.Errorf("parse token file: %w", err)
		}
	
		if token.AccessToken == "" {
			return fmt.Errorf("token file has no access_token")
		}
	
		a.token = &token
		if a.isExpired() {
			fmt.Println("Token expired. Attempting refresh...")
			if err := a.Refresh(); err != nil {
				a.token = nil
				return fmt.Errorf("token expired and refresh failed: %w", err)
			}
			fmt.Println("Token refreshed successfully.")
		}
	
		return nil
	}
	
	func (a *Auth) Refresh() error {
		if a.token == nil || a.token.RefreshToken == "" {
			return fmt.Errorf("no refresh token available")
		}
	
		resp, err := a.client.GenerateAccessTokenFromRefreshToken(
			a.token.RefreshToken,
			a.cfg.Pin,
			a.client,
		)
		if err != nil {
			return fmt.Errorf("refresh token: %w", err)
		}
	
		return a.parseAndSave(resp)
	}

	func (a *Auth) Validate() error {
		model := a.Model()
		if model == nil {
			return fmt.Errorf("no token loaded")
		}
	
		_, err := model.GetProfile()
		if err != nil {
			return fmt.Errorf("token validation failed: %w", err)
		}
		return nil
	}
	
	func (a *Auth) AccessToken() string {
		if a.token == nil {
			return ""
		}
		return a.cfg.AppID + ":" + a.token.AccessToken
	}
	
	func (a *Auth) Model() *fyersgosdk.FyersModel {
		if a.token == nil {
			return nil
		}
		return fyersgosdk.NewFyersModel(a.cfg.AppID, a.token.AccessToken)
	}
	
	func (a *Auth) HasToken() bool {
		return a.token != nil && a.token.AccessToken != ""
	}

	func (a *Auth) isExpired() bool {
		if a.token == nil {
			return true
		}
		now := time.Now()
		created := a.token.CreatedAt
		return created.Year() != now.Year() ||
			created.Month() != now.Month() ||
			created.Day() != now.Day()
	}
	
	func (a *Auth) parseAndSave(resp string) error {
		var parsed map[string]interface{}
		if err := json.Unmarshal([]byte(resp), &parsed); err != nil {
			return fmt.Errorf("parse response: %w", err)
		}
	
		if s, _ := parsed["s"].(string); s == "error" {
			msg, _ := parsed["message"].(string)
			return fmt.Errorf("API error: %s", msg)
		}
	
		accessToken, _ := parsed["access_token"].(string)
		refreshToken, _ := parsed["refresh_token"].(string)
	
		if accessToken == "" {
			return fmt.Errorf("no access_token in response: %s", resp)
		}
		if refreshToken == "" && a.token != nil {
			refreshToken = a.token.RefreshToken
		}
	
		a.token = &Token{
			AccessToken:  accessToken,
			RefreshToken: refreshToken,
			CreatedAt:    time.Now(),
		}
	
		return a.save()
	}
	
	func (a *Auth) save() error {
		data, err := json.MarshalIndent(a.token, "", "  ")
		if err != nil {
			return fmt.Errorf("marshal token: %w", err)
		}
		return os.WriteFile(a.cfg.TokenPath, data, 0600)
	}