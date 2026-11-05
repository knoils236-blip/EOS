import {
  S3Client,
  ListObjectsV2Command,
  GetObjectCommand,
  PutObjectCommand,
  DeleteObjectCommand,
} from '@aws-sdk/client-s3';
import type { Readable } from 'stream';
import type { BrowseResult, FileEntry } from './types';

const bucket = process.env.EOS_S3_BUCKET || 'eos';

let _client: S3Client | null = null;

export class InvalidS3KeyError extends Error {
  constructor() {
    super('S3 对象键无效');
    this.name = 'InvalidS3KeyError';
  }
}

function getClient(): S3Client {
  if (!_client) {
    _client = new S3Client({
      endpoint: process.env.EOS_S3_ENDPOINT_URL || 'http://localhost:8333',
      region: process.env.EOS_S3_REGION || 'us-east-1',
      credentials: {
        accessKeyId: process.env.EOS_S3_ACCESS_KEY_ID || '',
        secretAccessKey: process.env.EOS_S3_SECRET_ACCESS_KEY || '',
      },
      forcePathStyle: true,
    });
  }
  return _client;
}

export async function browseFiles(
  prefix: string = '',
  maxKeys: number = 200,
  continuationToken?: string
): Promise<BrowseResult> {
  const client = getClient();

  const command = new ListObjectsV2Command({
    Bucket: bucket,
    Prefix: prefix,
    Delimiter: '/',
    MaxKeys: maxKeys,
    ...(continuationToken ? { ContinuationToken: continuationToken } : {}),
  });

  const response = await client.send(command);

  const entries: FileEntry[] = [];

  // Add folders (common prefixes)
  if (response.CommonPrefixes) {
    for (const cp of response.CommonPrefixes) {
      if (!cp.Prefix) continue;
      const name = cp.Prefix.slice(prefix.length).replace(/\/$/, '');
      if (!name) continue;
      entries.push({
        name,
        key: cp.Prefix,
        size: 0,
        lastModified: null,
        isFolder: true,
      });
    }
  }

  // Add files
  if (response.Contents) {
    for (const obj of response.Contents) {
      if (!obj.Key) continue;
      const name = obj.Key.slice(prefix.length);
      // Skip the prefix itself (empty name) and "folder" markers
      if (!name || name.endsWith('/')) continue;
      entries.push({
        name,
        key: obj.Key,
        size: obj.Size ?? 0,
        lastModified: obj.LastModified?.toISOString() ?? null,
        isFolder: false,
      });
    }
  }

  return {
    prefix,
    entries,
    continuationToken: response.IsTruncated ? (response.NextContinuationToken ?? null) : null,
  };
}

export async function searchFiles(query: string, prefix: string = '', maxResults: number = 100): Promise<FileEntry[]> {
  const client = getClient();
  const lowerQuery = query.toLowerCase();
  const results: FileEntry[] = [];
  let token: string | undefined;

  // Paginate through all objects under prefix (no delimiter = recursive)
  while (results.length < maxResults) {
    const command = new ListObjectsV2Command({
      Bucket: bucket,
      Prefix: prefix,
      MaxKeys: 1000,
      ...(token ? { ContinuationToken: token } : {}),
    });

    const response = await client.send(command);

    if (response.Contents) {
      for (const obj of response.Contents) {
        if (!obj.Key) continue;
        const name = obj.Key.slice(prefix.length);
        if (!name || name.endsWith('/')) continue;

        if (name.toLowerCase().includes(lowerQuery)) {
          results.push({
            name,
            key: obj.Key,
            size: obj.Size ?? 0,
            lastModified: obj.LastModified?.toISOString() ?? null,
            isFolder: false,
          });
          if (results.length >= maxResults) break;
        }
      }
    }

    if (!response.IsTruncated) break;
    token = response.NextContinuationToken;
  }

  return results;
}

export async function getFileStream(
  key: string,
  range?: string
): Promise<{
  stream: Readable;
  size: number;
  contentType: string;
  contentRange: string | null;
  acceptRanges: string;
}> {
  const client = getClient();

  const command = new GetObjectCommand({
    Bucket: bucket,
    Key: key,
    ...(range ? { Range: range } : {}),
  });

  const response = await client.send(command);

  return {
    stream: response.Body as Readable,
    size: response.ContentLength ?? 0,
    contentType: response.ContentType ?? 'application/octet-stream',
    contentRange: response.ContentRange ?? null,
    acceptRanges: response.AcceptRanges ?? 'bytes',
  };
}

export function normalizeByteRange(value: string | null): string | undefined {
  if (!value) return undefined;
  const range = value.trim();
  if (!/^bytes=(?:\d+-\d*|-\d+)$/.test(range)) {
    throw new RangeError('仅支持单段 HTTP Range');
  }
  return range;
}

export function downloadResponseShape({
  fileName,
  size,
  contentType,
  contentRange,
  acceptRanges,
  inline,
}: {
  fileName: string;
  size: number;
  contentType: string;
  contentRange: string | null;
  acceptRanges: string;
  inline: boolean;
}): { status: number; headers: Record<string, string> } {
  return {
    status: contentRange ? 206 : 200,
    headers: {
      'Content-Type': contentType,
      'Content-Length': String(size),
      'Content-Disposition': `${inline ? 'inline' : 'attachment'}; filename*=UTF-8''${encodeURIComponent(fileName)}`,
      'Accept-Ranges': acceptRanges,
      'X-Content-Type-Options': 'nosniff',
      ...(contentRange ? { 'Content-Range': contentRange } : {}),
    },
  };
}

export function resolveInlineContentType(contentType: string, fileName: string): string | null {
  const normalized = contentType.split(';', 1)[0].trim().toLowerCase();
  if (['application/octet-stream', 'binary/octet-stream'].includes(normalized) && /\.mp4$/i.test(fileName)) {
    return 'video/mp4';
  }
  if (normalized.startsWith('video/')) return contentType;
  if (
    ['application/json', 'text/json', 'application/x-ndjson', 'application/jsonl', 'text/plain'].includes(
      normalized
    )
  ) {
    return contentType;
  }
  if (['application/octet-stream', 'binary/octet-stream'].includes(normalized) && /\.(?:json|jsonl|txt|log)$/i.test(fileName)) {
    return 'text/plain; charset=utf-8';
  }
  return null;
}

export function assertSafeS3Key(key: string): void {
  if (
    !key ||
    key.startsWith('/') ||
    key.includes('\\') ||
    key.includes('\0') ||
    key.split('/').some((segment) => segment === '' || segment === '.' || segment === '..')
  ) {
    throw new InvalidS3KeyError();
  }
}

export async function uploadFile(
  key: string,
  body: Buffer | Uint8Array | ReadableStream,
  contentType: string = 'application/octet-stream'
): Promise<void> {
  const client = getClient();

  const command = new PutObjectCommand({
    Bucket: bucket,
    Key: key,
    Body: body,
    ContentType: contentType,
  });

  await client.send(command);
}

export async function deleteFile(key: string): Promise<void> {
  const client = getClient();

  await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: key }));
}

/**
 * Delete all objects under a prefix (i.e. delete a "folder").
 * Lists all keys recursively then deletes them one by one.
 */
export async function deletePrefix(prefix: string): Promise<number> {
  const client = getClient();
  let deleted = 0;
  let token: string | undefined;

  do {
    const list = await client.send(
      new ListObjectsV2Command({
        Bucket: bucket,
        Prefix: prefix,
        MaxKeys: 1000,
        ...(token ? { ContinuationToken: token } : {}),
      })
    );

    if (list.Contents) {
      for (const obj of list.Contents) {
        if (!obj.Key) continue;
        await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: obj.Key }));
        deleted++;
      }
    }

    token = list.IsTruncated ? list.NextContinuationToken : undefined;
  } while (token);

  return deleted;
}
