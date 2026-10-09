import { writeFileSync } from 'node:fs';
writeFileSync(new URL('../IMPORTED_CODE_EXECUTED', import.meta.url), 'unexpected execution');
throw new Error('LAIP must never execute imported code');
