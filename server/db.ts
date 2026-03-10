import pg from 'pg'

const pool = new pg.Pool({
  host: 'localhost',
  port: 5432,
  user: 'me',
  password: 'algotrix',
  database: 'atdb',
  max: 10,
})

export default pool
