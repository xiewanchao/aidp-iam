import fs from "node:fs";
import path from "node:path";

const [sourceDir, outputFile] = process.argv.slice(2);

if (!sourceDir || !outputFile) {
  console.error("usage: node build-theme-jar.mjs <source-dir> <output-jar>");
  process.exit(2);
}

const crcTable = new Uint32Array(256);
for (let i = 0; i < 256; i += 1) {
  let crc = i;
  for (let j = 0; j < 8; j += 1) {
    crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  }
  crcTable[i] = crc >>> 0;
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosTimestamp(date) {
  const year = Math.max(1980, date.getFullYear());
  const time =
    (date.getHours() << 11) |
    (date.getMinutes() << 5) |
    Math.floor(date.getSeconds() / 2);
  const day = (year - 1980) << 9 | ((date.getMonth() + 1) << 5) | date.getDate();
  return { time, day };
}

function writeUInt16(value) {
  const buffer = Buffer.alloc(2);
  buffer.writeUInt16LE(value);
  return buffer;
}

function writeUInt32(value) {
  const buffer = Buffer.alloc(4);
  buffer.writeUInt32LE(value >>> 0);
  return buffer;
}

function collectFiles(root) {
  const files = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectFiles(fullPath));
    } else if (entry.isFile()) {
      files.push(fullPath);
    }
  }
  return files;
}

const root = path.resolve(sourceDir);
const files = collectFiles(root).sort();
const localParts = [];
const centralParts = [];
const fixedTimestamp = dosTimestamp(new Date(Date.UTC(1980, 0, 1, 0, 0, 0)));
let offset = 0;

for (const filePath of files) {
  const relativeName = path.relative(root, filePath).split(path.sep).join("/");
  const nameBuffer = Buffer.from(relativeName, "utf8");
  const data = fs.readFileSync(filePath);
  const { time, day } = fixedTimestamp;
  const crc = crc32(data);

  const localHeader = Buffer.concat([
    writeUInt32(0x04034b50),
    writeUInt16(20),
    writeUInt16(0),
    writeUInt16(0),
    writeUInt16(time),
    writeUInt16(day),
    writeUInt32(crc),
    writeUInt32(data.length),
    writeUInt32(data.length),
    writeUInt16(nameBuffer.length),
    writeUInt16(0),
    nameBuffer,
  ]);
  localParts.push(localHeader, data);

  centralParts.push(Buffer.concat([
    writeUInt32(0x02014b50),
    writeUInt16(20),
    writeUInt16(20),
    writeUInt16(0),
    writeUInt16(0),
    writeUInt16(time),
    writeUInt16(day),
    writeUInt32(crc),
    writeUInt32(data.length),
    writeUInt32(data.length),
    writeUInt16(nameBuffer.length),
    writeUInt16(0),
    writeUInt16(0),
    writeUInt16(0),
    writeUInt16(0),
    writeUInt32(0),
    writeUInt32(offset),
    nameBuffer,
  ]));

  offset += localHeader.length + data.length;
}

const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
const endRecord = Buffer.concat([
  writeUInt32(0x06054b50),
  writeUInt16(0),
  writeUInt16(0),
  writeUInt16(files.length),
  writeUInt16(files.length),
  writeUInt32(centralSize),
  writeUInt32(offset),
  writeUInt16(0),
]);

fs.mkdirSync(path.dirname(path.resolve(outputFile)), { recursive: true });
fs.writeFileSync(outputFile, Buffer.concat([...localParts, ...centralParts, endRecord]));
