# MissileSignal server

既存Windows版と独立したサーバー用コピーです。防衛省Web・NHK RSS・NHK Webのみ監視し、Xは起動しません。GUI、Signal連携データ、APIキーは含みません。

GitHubはコード保存用です。実行には24時間稼働のLinuxサーバーが必要です。

## 設定

- `SIGNAL_ACCOUNT`: 連携済みSignalアカウント
- `SIGNAL_GROUP_ID`: 本番送信先のグループID（必須）
- `SIGNAL_RPC`: 同じサーバーで共有するSignal JSON-RPC URL。Docker内では `http://signal:8080/api/v1/rpc` など。外部公開しないでください。
- `DATA_DIR`: 永続保存ディレクトリ。Dockerの既定は `/data`。UID 10001に書き込み権限が必要。
- `SIGNAL_CONFIG_FILE`: 任意。サーバー内の非公開JSON設定ファイル。環境変数が優先します。
- `INTERVAL_SECONDS`: 監視間隔。既定10秒、最低5秒。

`python runner.py` で実行します。Dockerはこのディレクトリをbuild contextに指定してください。永続ボリュームを `/data` にマウントし、restart policyは `unless-stopped` を使用します。

地震監視とSignalデーモンを共有します。このサービス自身はデーモンを起動しません。Signal連携ディレクトリは共有デーモンだけが書き込み、監視コンテナには渡しません。移行前にPC側の監視を停止して二重送信を防ぎ、同一のSignal連携データを複数デーモンで同時に使用しないでください。移行に際して既存キューは自動コピーされません。

起動時は過去記事を履歴として登録し通知しません。未送信キューは永続DBから再試行します。送信成功はSignalサービスの受付確認で、受信端末の配信保証ではありません。RPC成功後にDB記録前に停止した場合、再送の可能性があります。通知本文は永続ディレクトリの非公開ログにも保存されます。

ローカル確認: `python -m unittest test_monitor test_server -v`。ネットワーク・実送信を使わずに実行できます。
