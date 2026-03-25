# Hono Reference

## Routing
- `app.get/post/put/delete(path, handler)` — standard HTTP methods
- `app.all(path, handler)` — any method
- `app.on(methods[], path, handler)` — custom methods
- Path params: `/user/:name` → `c.req.param('name')`
- Optional params: `/api/animal/:type?`
- Wildcards: `/wild/*/card`
- Regex: `/post/:date{[0-9]+}`
- Chaining: `app.get('/ep', h).post(h).delete(h)`
- Grouping: `const sub = new Hono(); app.route('/prefix', sub)`
- Base path: `new Hono().basePath('/api')`

## Context (c)
- `c.req.param('name')` — path param
- `c.req.query('page')` — query param
- `c.req.header('X-Key')` — request header
- `c.req.valid('json')` — validated data
- `c.text('ok')` — text response
- `c.json({ ok: true })` — JSON response
- `c.html('<h1>Hi</h1>')` — HTML response
- `c.body(data, status, headers)` — raw response
- `c.redirect('/', 301)` — redirect
- `c.status(201)` — set status
- `c.header('key', 'val')` — set response header
- `c.set('key', val)` / `c.get('key')` — request-scoped vars
- `c.var.key` — dot notation for vars
- `c.notFound()` — 404
- `c.error` — caught exception (in middleware)

## Middleware
- `app.use(middleware)` — global
- `app.use('/path/*', middleware)` — scoped
- Execution: registration order, stack pattern (before next → handler → after next)
- Custom: `createMiddleware(async (c, next) => { ... await next() ... })`
- Built-in: `logger()`, `cors()`, `basicAuth()`, `jwt()`, `etag()`, `poweredBy()`

## Validation
- `validator('json'|'form'|'query'|'header'|'param'|'cookie', callback)`
- Zod: `zValidator('json', z.object({ body: z.string() }))`
- Access: `c.req.valid('json')` in handler
- Content-Type required for json/form validation
- Use lowercase header names

## Error Handling
- Throw in handler → caught by `app.onError()`
- Or return `c.text('Error', 400)` from validator

## Type Safety
```ts
type Env = { Bindings: { DB: D1Database }; Variables: { user: User } }
const app = new Hono<Env>()
```
