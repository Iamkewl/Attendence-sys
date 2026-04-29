/**
 * SSE hook — subscribe to real-time attendance events.
 */
import { useEffect, useState, useRef } from 'react';

export function useAttendanceSSE(scheduleId) {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef(null);

  useEffect(() => {
    if (!scheduleId) return;

    const token = sessionStorage.getItem('access_token');
    if (!token) {
      setConnected(false);
      return;
    }

    const source = new EventSource(`/api/v1/sse/attendance/${scheduleId}?token=${encodeURIComponent(token)}`);
    sourceRef.current = source;

    source.onopen = () => setConnected(true);

    source.addEventListener('detection', (e) => {
      const data = JSON.parse(e.data);
      setEvents(prev => [data, ...prev].slice(0, 50));
    });

    source.addEventListener('snapshot_complete', (e) => {
      const data = JSON.parse(e.data);
      setEvents(prev => [data, ...prev].slice(0, 50));
    });

    source.addEventListener('heartbeat', () => {
      // Keep-alive — no action needed
    });

    source.onerror = () => {
      setConnected(false);
      // Auto-reconnect after 3s
      setTimeout(() => {
        if (sourceRef.current === source) {
          source.close();
        }
      }, 3000);
    };

    return () => {
      source.close();
      setConnected(false);
    };
  }, [scheduleId]);

  const clearEvents = () => setEvents([]);

  return { events, connected, clearEvents };
}
