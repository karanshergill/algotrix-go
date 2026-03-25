# TanStack Router Reference (React)

## File-Based Routing
Routes auto-generated from `src/routes/` directory structure.

### File Naming
- `__root.tsx` — root layout (wraps everything)
- `index.tsx` — index route for directory
- `about.tsx` — `/about` route
- `posts.$postId.tsx` — `/posts/:postId` dynamic route
- `posts.index.tsx` — `/posts` index
- `_layout.tsx` — pathless layout (groups children without URL segment)
- `posts_.tsx` — layout escape (opts out of parent layout)
- `$.tsx` — catch-all / splat route

### Directory Style (alternative)
```
src/routes/
├── __root.tsx
├── index.tsx
├── posts/
│   ├── index.tsx
│   ├── $postId.tsx
│   └── route.tsx (layout for /posts/*)
```

## Route Component
```tsx
// src/routes/posts.$postId.tsx
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/posts/$postId')({
  component: PostComponent,
  loader: async ({ params }) => {
    return fetchPost(params.postId)
  },
})

function PostComponent() {
  const post = Route.useLoaderData()
  return <div>{post.title}</div>
}
```

## Data Loading
```tsx
export const Route = createFileRoute('/posts')({
  loader: async () => {
    const posts = await fetchPosts()
    return { posts }
  },
  component: PostsComponent,
})
```

## Search Params
```tsx
import { z } from 'zod'

export const Route = createFileRoute('/posts')({
  validateSearch: z.object({
    page: z.number().default(1),
    filter: z.string().optional(),
  }),
  component: PostsComponent,
})

function PostsComponent() {
  const { page, filter } = Route.useSearch()
  // ...
}
```

## Navigation
```tsx
import { Link, useNavigate } from '@tanstack/react-router'

// Declarative
<Link to="/posts/$postId" params={{ postId: '1' }}>Post 1</Link>
<Link to="/posts" search={{ page: 2 }}>Page 2</Link>

// Programmatic
const navigate = useNavigate()
navigate({ to: '/posts/$postId', params: { postId: '1' } })
```

## Pending/Error States
```tsx
export const Route = createFileRoute('/posts')({
  pendingComponent: () => <div>Loading...</div>,
  errorComponent: ({ error }) => <div>Error: {error.message}</div>,
  loader: async () => fetchPosts(),
})
```

## Code Splitting
Enabled via `autoCodeSplitting: true` in vite plugin config. Each route lazy-loads automatically.

## Hooks
- `Route.useLoaderData()` — access loaded data
- `Route.useSearch()` — access search params
- `Route.useParams()` — access path params
- `useNavigate()` — programmatic navigation
- `useRouter()` — router instance
- `useMatch()` — current route match
