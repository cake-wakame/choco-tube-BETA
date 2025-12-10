Renderデプロイ用コマンド
ビルドコマンド:

pip install -r requirements.txt

スタートコマンド:

gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120

##
Transloadit
あなたの認証キーと秘密
認証キーと秘密を必ずメモしてください。オーソシークレットは今後表示されません。

認証キー:

R244EKuonluFkwhTYOu85vi6ZPm6mmZV
オーソシークレット:

4zVZ7eQm16qawPil8B4NJRr68kkCdMXQkd8NbNaq
##
freeconvert:
こちらがあなたの個人アクセストークンです。これが唯一の時間です。表示されているので、失くしないでください。このトークンを使ってAPIを作成できます。 要求:

api_production_15cc009b9ac13759fb43f4946b3c950fee5e56e2f0214f242f6e9e4efc3093df.69393f3ea22aa85dd55c84ff.69393fa9142a194b36417393
