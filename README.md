Renderデプロイ用コマンド
ビルドコマンド:

pip install -r requirements.txt

スタートコマンド:

gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120

##
Transloadit
Auth Key:

a49d62c45c3d7a2d6efaf02bf23e2a37

Auth Secret:

d307b850040763cfb71b82abb37c07580f901dcf
