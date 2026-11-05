import { useTransferStore } from './stores/transferStore';

const AUTO_REMOVE_DELAY = 5000;

function getActions() {
  return useTransferStore.getState();
}

export function startDownload(key: string, fileName: string) {
  const { addTransfer, updateTransfer, removeTransfer } = getActions();

  const id = addTransfer({
    fileName,
    type: 'download',
    status: 'in_progress',
    progress: 0,
    bytesLoaded: 0,
    bytesTotal: 0,
  });

  (async () => {
    try {
      const response = await fetch(`/api/files/download/${encodeURI(key)}`);

      if (!response.ok) {
        throw new Error(`下载失败：${response.statusText}`);
      }

      const contentLength = response.headers.get('Content-Length');
      const total = contentLength ? parseInt(contentLength, 10) : 0;
      updateTransfer(id, { bytesTotal: total });

      const reader = response.body?.getReader();
      if (!reader) throw new Error('响应正文为空');

      const chunks: Uint8Array[] = [];
      let loaded = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        chunks.push(value);
        loaded += value.length;
        updateTransfer(id, {
          bytesLoaded: loaded,
          progress: total > 0 ? Math.round((loaded / total) * 100) : 0,
        });
      }

      // Trigger browser download
      const blob = new Blob(chunks as BlobPart[]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      updateTransfer(id, { status: 'completed', progress: 100, bytesLoaded: loaded });
      setTimeout(() => removeTransfer(id), AUTO_REMOVE_DELAY);
    } catch (error) {
      updateTransfer(id, {
        status: 'error',
        error: error instanceof Error ? error.message : '下载失败',
      });
    }
  })();
}

export function startUpload(file: File, destinationKey: string) {
  const { addTransfer, updateTransfer, removeTransfer } = getActions();

  const id = addTransfer({
    fileName: file.name,
    type: 'upload',
    status: 'in_progress',
    progress: 0,
    bytesLoaded: 0,
    bytesTotal: file.size,
  });

  const formData = new FormData();
  formData.append('file', file);
  formData.append('key', destinationKey);

  const xhr = new XMLHttpRequest();

  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      updateTransfer(id, {
        bytesLoaded: event.loaded,
        progress: Math.round((event.loaded / event.total) * 100),
      });
    }
  };

  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      updateTransfer(id, { status: 'completed', progress: 100, bytesLoaded: file.size });
      setTimeout(() => removeTransfer(id), AUTO_REMOVE_DELAY);
    } else {
      updateTransfer(id, { status: 'error', error: `上传失败：${xhr.statusText}` });
    }
  };

  xhr.onerror = () => {
    updateTransfer(id, { status: 'error', error: '上传失败：网络错误' });
  };

  xhr.open('POST', '/api/files/upload');
  xhr.send(formData);
}
