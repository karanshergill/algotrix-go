package models

type Symbol struct {
    FyToken int64  `json:"fy_token"`
    Symbol  string `json:"symbol"`
	Name    string `json:"name"`
    ISIN    string `json:"isin"`
}