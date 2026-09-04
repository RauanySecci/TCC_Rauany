import 'dotenv/config';
import { MongoClient } from 'mongodb';

const client = new MongoClient(process.env.MONGO_URI);

async function testarConexao() {
  try {
    await client.connect();
    const db = client.db(process.env.MONGO_DB);
    const resultado = await db.command({ ping: 1 });
    const info = await db.command({ buildInfo: 1 });

    console.log('✅ Conexão bem-sucedida!');
    console.log('Ping:', resultado);
    console.log('Versão do MongoDB:', info.version);
  } catch (erro) {
    console.error('❌ Erro ao conectar:', erro.message);
  } finally {
    await client.close();
  }
}

testarConexao();