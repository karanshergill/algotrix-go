# pgx/v5 Reference

## Connection
```go
conn, err := pgx.Connect(ctx, "postgres://user:pass@host/db")
defer conn.Close(ctx)
```

## Pool (thread-safe)
```go
pool, err := pgxpool.New(ctx, connString)
defer pool.Close()
```

## Query
```go
// Multiple rows
rows, err := conn.Query(ctx, "SELECT name, age FROM users WHERE id=$1", 42)
defer rows.Close()
names, err := pgx.CollectRows(rows, pgx.RowTo[string])

// Single row
var name string
err := conn.QueryRow(ctx, "SELECT name FROM users WHERE id=$1", 42).Scan(&name)

// Struct mapping
type User struct { ID int32; Name string; Age int32 }
users, err := pgx.CollectRows(rows, pgx.RowToStructByName[User])

// Execute (no result)
tag, err := conn.Exec(ctx, "DELETE FROM users WHERE id=$1", 42)
tag.RowsAffected()
```

## Named Args
```go
conn.Query(ctx, "SELECT * FROM t WHERE foo=@foo AND bar=@bar",
    pgx.NamedArgs{"foo": 1, "bar": 2})
```

## Transactions
```go
tx, err := conn.Begin(ctx)
defer tx.Rollback(ctx)
tx.Exec(ctx, "INSERT INTO foo(id) VALUES (1)")
tx.Commit(ctx)

// Helper (auto commit/rollback)
pgx.BeginFunc(ctx, conn, func(tx pgx.Tx) error {
    _, err := tx.Exec(ctx, "INSERT INTO foo(id) VALUES (1)")
    return err
})
```

## Batch (single round trip)
```go
batch := &pgx.Batch{}
batch.Queue("SELECT 1+1").QueryRow(func(row pgx.Row) error { ... })
batch.Queue("SELECT 1+2").QueryRow(func(row pgx.Row) error { ... })
conn.SendBatch(ctx, batch).Close()
```

## COPY (bulk insert)
```go
rows := [][]any{{"John", int32(36)}, {"Jane", int32(29)}}
conn.CopyFrom(ctx, pgx.Identifier{"people"}, []string{"name","age"}, pgx.CopyFromRows(rows))
```

## Listen/Notify
```go
conn.Exec(ctx, "LISTEN channel")
notification, err := conn.WaitForNotification(ctx)
```

## Errors
```go
errors.Is(err, pgx.ErrNoRows)
errors.Is(err, pgx.ErrTooManyRows)
```

## Query Modes
- `QueryModeCacheStatement` — default, prepare & cache
- `QueryModeExec` — direct execution, no prepare
- `QueryModeSimpleProtocol` — PgBouncer compatible

## Identifiers
```go
pgx.Identifier{"schema", "table"}.Sanitize() // "schema"."table"
```
