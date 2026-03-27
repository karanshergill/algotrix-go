package auth

import (
    "encoding/base64"
    "encoding/json"
    "fmt"
    "log"
    "os"
    "os/exec"
    "strings"
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
			if err := a.refreshWithRetry(); err != nil {
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
		if exp, err := jwtExp(a.token.AccessToken); err == nil {
			return time.Now().Unix() >= exp-300
		}
		// Fallback: old day-comparison check if JWT parsing fails.
		now := time.Now()
		created := a.token.CreatedAt
		return created.Year() != now.Year() ||
			created.Month() != now.Month() ||
			created.Day() != now.Day()
	}

	// jwtExp extracts the exp claim from a JWT without a library.
	func jwtExp(token string) (int64, error) {
		parts := strings.SplitN(token, ".", 3)
		if len(parts) < 2 {
			return 0, fmt.Errorf("not a JWT")
		}
		payload := parts[1]
		// JWT uses base64url without padding.
		if m := len(payload) % 4; m != 0 {
			payload += strings.Repeat("=", 4-m)
		}
		decoded, err := base64.URLEncoding.DecodeString(payload)
		if err != nil {
			return 0, fmt.Errorf("decode JWT payload: %w", err)
		}
		var claims struct {
			Exp int64 `json:"exp"`
		}
		if err := json.Unmarshal(decoded, &claims); err != nil {
			return 0, fmt.Errorf("parse JWT claims: %w", err)
		}
		if claims.Exp == 0 {
			return 0, fmt.Errorf("no exp claim in JWT")
		}
		return claims.Exp, nil
	}

	func (a *Auth) refreshWithRetry() error {
		const maxRetries = 5
		var lastErr error
		for i := 0; i < maxRetries; i++ {
			lastErr = a.Refresh()
			if lastErr == nil {
				return nil
			}
			backoff := time.Duration(1<<uint(i)) * time.Second
			log.Printf("refresh attempt %d/%d failed: %v (retrying in %v)", i+1, maxRetries, lastErr, backoff)
			time.Sleep(backoff)
		}
		log.Printf("SDK refresh failed after %d attempts, trying bash fallback...", maxRetries)
		return a.bashFallbackRefresh()
	}

	func (a *Auth) bashFallbackRefresh() error {
		cmd := exec.Command("./refresh_token.sh")
		output, err := cmd.CombinedOutput()
		if err != nil {
			return fmt.Errorf("bash fallback failed: %w (output: %s)", err, output)
		}
		data, err := os.ReadFile(a.cfg.TokenPath)
		if err != nil {
			return fmt.Errorf("re-read token after bash fallback: %w", err)
		}
		var token Token
		if err := json.Unmarshal(data, &token); err != nil {
			return fmt.Errorf("parse token after bash fallback: %w", err)
		}
		if token.AccessToken == "" {
			return fmt.Errorf("bash fallback produced empty access_token")
		}
		a.token = &token
		return nil
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