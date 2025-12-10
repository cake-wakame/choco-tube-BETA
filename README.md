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
