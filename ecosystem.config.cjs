module.exports = {
  apps: [
    {
      name: 'api-server',
      script: 'npx',
      args: 'tsx server/index.ts',
      cwd: '/home/me/projects/algotrix-go',
      watch: false,
      max_memory_restart: '500M',
      out_file: '/tmp/api-server-out.log',
      error_file: '/tmp/api-server-error.log',
      env: {
        NODE_ENV: 'development',
        PORT: 3001,
      },
    },
    {
      name: 'dashboard',
      script: 'npx',
      args: 'vite --host 0.0.0.0 --port 5180',
      cwd: '/home/me/projects/algotrix-go/dashboard',
      watch: false,
      max_memory_restart: '500M',
      out_file: '/tmp/dashboard-out.log',
      error_file: '/tmp/dashboard-error.log',
      env: {
        NODE_ENV: 'development',
      },
    },
    {
      name: 'go-feed',
      script: './algotrix',
      args: 'feed',
      cwd: '/home/me/projects/algotrix-go/engine',
      watch: false,
      max_memory_restart: '1G',
      out_file: '/tmp/go-feed-out.log',
      error_file: '/tmp/go-feed-error.log',
      cron_restart: '0 9 * * 1-5',  // restart at 9:00 AM on weekdays (fresh token)
      env: {
        GOMAXPROCS: '4',
      },
    },
  ],
};
