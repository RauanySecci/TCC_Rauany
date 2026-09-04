import { ApolloServer } from '@apollo/server';
import { startStandaloneServer } from '@apollo/server/standalone';
import { ApolloServerPluginLandingPageLocalDefault } from '@apollo/server/plugin/landingPage/default';

const typeDefs = `#graphql
  type Query {
    ola: String
  }
`;

const resolvers = {
  Query: {
    ola: () => 'Servidor GraphQL do catálogo Postgres está no ar!',
  },
};

const server = new ApolloServer({
  typeDefs,
  resolvers,
  plugins: [ApolloServerPluginLandingPageLocalDefault({ embed: true })],
});

const { url } = await startStandaloneServer(server, {
  listen: { port: 4000 },
});

console.log(`Servidor pronto em ${url}`);