import React, { useEffect, useState } from 'react';

type Status = 'idle' | 'recording' | 'starting' | 'stopping';

export function Popup() {
  const [status, setStatus] = useState<Status>('idle');
  const [message, setMessage] = useState<string>('');

  useEffect(() => {
    chrome.runtime
      .sendMessage({ kind: 'query-state' })
      .then((s?: { recording?: boolean }) => {
        setStatus(s?.recording ? 'recording' : 'idle');
      })
      .catch(() => setStatus('idle'));
  }, []);

  const start = async () => {
    setStatus('starting');
    const r = await chrome.runtime.sendMessage({ kind: 'start-recording' });
    if (r?.ok) {
      setStatus('recording');
      setMessage('录制中…切到浏览器操作');
    } else {
      setStatus('idle');
      setMessage('启动失败：后端启动了吗？（uv run python examples/record_user_actions.py）');
    }
  };

  const stop = async () => {
    setStatus('stopping');
    const r = await chrome.runtime.sendMessage({ kind: 'stop-recording' });
    setStatus('idle');
    if (r?.ok) {
      setMessage(`已保存：${r.path ?? ''}（${r.steps ?? 0} 步）`);
    } else {
      setMessage('停止失败');
    }
  };

  const recording = status === 'recording';
  const busy = status === 'starting' || status === 'stopping';

  return (
    <div>
      <h3 style={{ margin: '0 0 8px' }}>TreeWalker 录制</h3>
      <button
        onClick={recording ? stop : start}
        disabled={busy}
        style={{
          width: '100%',
          padding: '8px',
          background: recording ? '#d00' : '#080',
          color: '#fff',
          border: 'none',
          borderRadius: 4,
          cursor: busy ? 'wait' : 'pointer',
        }}
      >
        {busy ? '…' : recording ? '停止录制' : '开始录制'}
      </button>
      {message && <p style={{ fontSize: 12, color: '#555', marginTop: 8 }}>{message}</p>}
    </div>
  );
}
