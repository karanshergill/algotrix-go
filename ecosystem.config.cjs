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
      name: 'nse-news',
      script: 'python3',
      args: '-u collector.py',
      cwd: '/home/me/projects/algotrix-go/news-collector',
      watch: false,
      max_memory_restart: '200M',
      out_file: '/tmp/nse-news-out.log',
      error_file: '/tmp/nse-news-error.log',
      env: {
        DB_HOST: 'localhost',
        DB_PORT: '5432',
        DB_NAME: 'atdb',
        DB_USER: 'me',
        DB_PASS: 'algotrix',
      },
    },
    {
      name: 'go-feed',
      script: './algotrix',
      args: 'feed',
      cwd: '/home/me/projects/algotrix-go/engine',
      watch: false,
      max_memory_restart: '2G',
      out_file: '/tmp/go-feed-out.log',
      error_file: '/tmp/go-feed-error.log',
      cron_restart: '0 9 * * 1-5',  // restart at 9:00 AM on weekdays (fresh token)
      env: {
        GOMAXPROCS: '4',
      },
    },
  ],
};
