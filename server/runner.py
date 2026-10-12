"""Headless service: shares an externally managed Signal daemon."""
import signal
import sys
import time
import monitor

def main():
    config=monitor.load_config()
    if not config['signal_account'] or not config['signal_group_id']:
        raise SystemExit('SIGNAL_ACCOUNT and SIGNAL_GROUP_ID are required (or SIGNAL_CONFIG_FILE).')
    if config['interval_seconds'] < 5:
        raise SystemExit('INTERVAL_SECONDS must be at least 5.')
    engine=monitor.Engine(config)
    def stop(*_): engine.stop.set()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    engine.start()
    try:
        while not engine.stop.wait(1):
            while not engine.events.empty():
                stamp,event=engine.events.get_nowait()
                # Notification bodies remain only in the private persistent log.
                if event.startswith('Signal送信成功'):
                    print(stamp,event,flush=True)
    finally:
        engine.close()
        for worker in engine.threads: worker.join(timeout=20)
        engine.pool.shutdown(wait=True,cancel_futures=True)
        engine.db.close()
    return 0

if __name__=='__main__': sys.exit(main())
