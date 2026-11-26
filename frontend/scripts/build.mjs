import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../../', import.meta.url));
const counter = fileURLToPath(new URL('../../.punkt-build-number', import.meta.url));

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
      env: { ...process.env, PUNKT_BUILD_VERSION: version },
    });
  } else {
    run('tsc', ['--noEmit']);
    run('vite', ['build'], { env: { ...process.env, VITE_PUNKT_BUILD_VERSION: version } });
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
