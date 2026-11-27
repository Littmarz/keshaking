import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { appendFileSync, existsSync, mkdirSync, readFileSync, rmdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../../', import.meta.url));
const counter = fileURLToPath(new URL('../../.punkt-build-number', import.meta.url));
const secretsFile = fileURLToPath(new URL('../../deploy/punkt-secrets.env.local', import.meta.url));
const secretNames = ['PUNKT_EMPLOYEE_PASSWORD', 'PUNKT_QUALITY_PASSWORD', 'PUNKT_MAIL_PASSWORD'];

// Стенд без паролей пускает любого в «Службу качества». Если пароли не заданы в окружении,
// создаём их один раз в deploy/punkt-secrets.env.local (в git не попадает) и берём оттуда при каждой сборке.
function stackSecrets() {
  if (secretNames.every((name) => process.env[name])) return {};
  const read = () => Object.fromEntries((existsSync(secretsFile) ? readFileSync(secretsFile, 'utf8') : '').split(/\r?\n/)
    .map((line) => line.match(/^([A-Z_]+)=(.+)$/)).filter(Boolean).map((match) => [match[1], match[2].trim()]));
  const missing = secretNames.filter((name) => !read()[name]);
  if (missing.length) {
    const lines = missing.map((name) => `${name}=${randomBytes(12).toString('base64url')}`);
    appendFileSync(secretsFile, `${lines.join('\n')}\n`, { mode: 0o600 });
    console.log(`Созданы пароли (${missing.join(', ')}): ${secretsFile}`);
  }
  const values = read();
  console.log(`Пароли «Сотрудник», «Служба качества» и Mailpit (логин punkt): ${secretsFile}`);
  return Object.fromEntries(secretNames.map((name) => [name, process.env[name] || values[name]]));
}

function nextVersion() {
  const lock = counter + '.lock';
  try {
    mkdirSync(lock);
  } catch (error) {
    if (error.code === 'EEXIST') {
      throw new Error('Другая сборка резервирует номер. Повторите запуск через несколько секунд.', { cause: error });
    }
    throw error;
  }

  try {
    const previous = existsSync(counter) ? readFileSync(counter, 'utf8').trim() : '0';
    if (!/^\d+$/.test(previous)) {
      throw new Error('В .punkt-build-number должно быть целое неотрицательное число.');
    }
    const next = BigInt(previous) + 1n;
    writeFileSync(counter, `${next}\n`);
    return `0.0.${next}`;
  } finally {
    rmdirSync(lock);
  }
}

function run(command, args, options) {
  const result = spawnSync(command, args, { stdio: 'inherit', ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

try {
  const version = nextVersion();
  console.log(`Пункт · сборка ${version}`);
  if (process.argv[2] === '--docker') {
    run('docker', ['compose', '-f', 'deploy/docker-compose.yml', 'up', '--build', '-d', '--wait'], {
      cwd: project,
      env: { ...process.env, ...stackSecrets(), PUNKT_BUILD_VERSION: version },
    });
  } else {
    run('tsc', ['--noEmit']);
    run('vite', ['build'], { env: { ...process.env, VITE_PUNKT_BUILD_VERSION: version } });
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
