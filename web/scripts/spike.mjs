import { config } from 'dotenv';
config({ path: 'prisma/.env' });
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient({
  log: [{ emit: 'event', level: 'query' }],
});

prisma.$on('query', (e) => console.log('SQL:', e.query));

try {
  const rows = await prisma.user.findMany({ take: 1 });
  console.log('spike ok — rijen:', rows.length);
} catch (err) {
  console.error('FAAL:', err.message);
} finally {
  await prisma.$disconnect();
}