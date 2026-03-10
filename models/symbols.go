package models

import "time"

type Symbol struct {
	ISIN       string    `json:"isin"`
	Symbol     string    `json:"symbol"`
	Name       string    `json:"name"`
	FyToken    int64     `json:"fy_token"`
	FySymbol   string    `json:"fy_symbol"`
	Series     string    `json:"series"`
	Status     string    `json:"status"`
	SkipReason *string   `json:"skip_reason,omitempty"`
	SkipDetail *string   `json:"skip_detail,omitempty"`
	CreatedAt  time.Time `json:"created_at"`
	UpdatedAt  time.Time `json:"updated_at"`
}
