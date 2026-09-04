import 'dotenv/config';
import { Pool } from 'pg';

const pool = new Pool({
  user: process.env.PGUSER,
  password: process.env.PGPASSWORD,
  database: process.env.PGDATABASE,
  host: process.env.PGHOST,
  port: process.env.PGPORT,
});

async function testarConexao() {
  try {
    const resultado = await pool.query('SELECT NOW() AS agora, version() AS versao');
    console.log('✅ Conexão bem-sucedida!');
    console.log('Horário do banco:', resultado.rows[0].agora);
    console.log('Versão do PostgreSQL:', resultado.rows[0].versao);
  } catch (erro) {
    console.error('❌ Erro ao conectar:', erro.message);
  } finally {
    await pool.end();
  }
}

testarConexao();