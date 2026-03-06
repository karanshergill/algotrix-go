package main

import (
    "bufio"
	"context"
    "fmt"
	"log"
    "os"
    "strings"

    "github.com/karanshergill/algotrix-go/internal/auth"
    "github.com/karanshergill/algotrix-go/internal/config"
    "github.com/karanshergill/algotrix-go/database/connections"
    "github.com/karanshergill/algotrix-go/symbols"
)

func main() {
    cfg, err := config.Load("internal/config/fyers.yaml")
    if err != nil {
        log.Fatal(err)
    }

    a := auth.New(cfg.Fyers)
    if err := a.LoadToken(); err != nil {
        fmt.Println("No valid token. Starting login flow...")
        fmt.Println()
        fmt.Println("Open this URL in your browser:")
        fmt.Println(a.LoginURL())
        fmt.Println()
        fmt.Print("Paste the auth_code from the redirect URL: ")
        reader := bufio.NewReader(os.Stdin)
        code, _ := reader.ReadString('\n')
        code = strings.TrimSpace(code)
        if err := a.Exchange(code); err != nil {
            log.Fatal(err)
        }
        fmt.Println("Token saved.")
    }

    if err := a.Validate(); err != nil {
        log.Fatal("Token invalid: ", err)
    }

    profile, err := a.Model().GetProfile()
    if err != nil {
        log.Fatal("Failed to get profile: ", err)
    }

    fmt.Println()
    fmt.Println("Connected successfully!")

    fmt.Println("Profile:", profile)

    // --- Database connections ---
    ctx := context.Background()

    dbCfg, err := connections.LoadDBConfig("database/connections/db.yaml")
    if err != nil {
        log.Fatal("Failed to load db config: ", err)
    }

    pgPool, err := connections.NewPostgresPool(ctx, &dbCfg.Postgres)
    if err != nil {
        log.Fatal("Postgres connection failed: ", err)
    }
    defer pgPool.Close()

    qdbPool, err := connections.NewQuestDBPool(ctx, &dbCfg.QuestDB)
    if err != nil {
        log.Fatal("QuestDB connection failed: ", err)
    }
    defer qdbPool.Close()

    qdbSender, err := connections.NewQuestDBSender(ctx, &dbCfg.QuestDB)
    if err != nil {
        log.Fatal("QuestDB ILP connection failed: ", err)
    }
    defer qdbSender.Close(ctx)

    // --- Load symbols ---
    if err := symbols.Load(ctx, pgPool); err != nil {
        log.Fatal("Symbol load failed: ", err)
    }

    _ = qdbPool    // will be used for queries later
    _ = qdbSender  // will be used for tick writes later
}