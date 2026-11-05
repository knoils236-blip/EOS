import type { NextRequest } from 'next/server';
import {
  assertSafeS3Key,
  downloadResponseShape,
  getFileStream,
  InvalidS3KeyError,
  normalizeByteRange,
  resolveInlineContentType,
} from '@/lib/s3/client';
import { requireRoleResponse } from '@/lib/auth/authz';

export async function GET(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const denied = await requireRoleResponse('VIEWER');
  if (denied) return denied;
  try {
    const { path } = await params;
    const key = path.join('/');
    assertSafeS3Key(key);
    const range = normalizeByteRange(request.headers.get('range'));

    const { stream, size, contentType, contentRange, acceptRanges } = await getFileStream(key, range);

    const fileName = key.split('/').pop() || 'download';
    const inline = request.nextUrl.searchParams.get('inline') === '1';
    const inlineContentType = inline ? resolveInlineContentType(contentType, fileName) : null;
    if (inline && inlineContentType === null) {
      stream.destroy();
      return Response.json(
        { error: '该文件类型不允许内联显示' },
        { status: 415, headers: { 'X-Content-Type-Options': 'nosniff' } }
      );
    }

    // Convert Node.js Readable to a web ReadableStream
    const webStream = new ReadableStream({
      start(controller) {
        stream.on('data', (chunk: Buffer) => {
          controller.enqueue(new Uint8Array(chunk));
        });
        stream.on('end', () => {
          controller.close();
        });
        stream.on('error', (err: Error) => {
          controller.error(err);
        });
      },
      cancel() {
        stream.destroy();
      },
    });
    request.signal.addEventListener('abort', () => stream.destroy(), { once: true });

    const responseShape = downloadResponseShape({
      fileName,
      size,
      contentType: inlineContentType ?? contentType,
      contentRange,
      acceptRanges,
      inline,
    });
    return new Response(webStream, responseShape);
  } catch (error) {
    console.error('Download error:', error);
    const upstreamStatus =
      typeof error === 'object' && error !== null && '$metadata' in error
        ? (error as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode
        : undefined;
    const status =
      error instanceof RangeError
        ? 416
        : error instanceof InvalidS3KeyError
          ? 400
          : upstreamStatus === 404
            ? 404
            : upstreamStatus === 416
              ? 416
              : 500;
    return Response.json({ error: error instanceof Error ? error.message : '下载失败' }, { status });
  }
}
