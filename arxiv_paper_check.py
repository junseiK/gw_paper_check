import os
import random
import requests
from datetime import datetime, timedelta, timezone
import arxiv
from google import genai
import re
import time

def send_discord_notify(message):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        print("【エラー】DISCORD_WEBHOOK_URL が設定されていません。")
        return

    # Discordの文字数制限(2000文字)対策
    chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]
    for chunk in chunks:
        data = {
            "content": chunk,
            "flags": 4
            # flags: URLの埋め込み表示を無効化
        }
        try:
            requests.post(webhook_url, json=data, timeout=10)
        except Exception as e:
            print(f"Discord通知失敗: {e}")
        time.sleep(1.5)

def clean_text(text):
    if not text: return ""
    return re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFDCF\uFDE0-\uFFFD]', '', text)

def fetch_and_summarize_papers():
    print("=" * 50)
    print("論文デイリーチェックを開始します...")
    print("=" * 50)

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    yesterday = now - timedelta(days=1)
    base_date = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    
    client = arxiv.Client(
        page_size=30,
        delay_seconds=10+random.random() * 5,
        num_retries=3
        )
    
    search_query = 'all:"gravitational wave"' # 興味のある分野
    # search_query = 'cat:astro-ph.HE OR cat:gr-qc OR cat:hep-th OR cat:hep-ph OR cat:physics.gen-ph' 
    # ↑興味のあるカテゴリ例。複文も可能だが、 (A & B) OR (C & D) などの複雑なクエリは arXiv API では著しく遅くなる場合があるので注意。
    
    search = arxiv.Search(
        query=search_query, 
        max_results=30,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )

    print(f"arXivから論文を取得中（クエリ: {search_query}）...")

    wait_times = [
        60+random.random() * 5,
        180+random.random() * 5,
        300+random.random() * 5
    ]
    
    results = []
    errmsg=""
    for attempt, wait in enumerate(wait_times + [0]):
        try:
            results = list(client.results(search))
            print("無事にarXivから論文を取得成功しました！")
            break

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "503" in error_msg:
                if wait > 0:
                    print(f"【警告】サーバー混雑を検知。{wait}秒待機して再試行します... (試行 {attempt + 1}/{len(wait_times)})")
                    time.sleep(wait)
                else:
                    errmsg = f"【失敗】再試行の上限に達しました。詳細: {error_msg}" 
                    print(errmsg)
                    break
            else:
                errmsg = f"想定外のエラーが発生しました: {error_msg}"
                print(errmsg)
                break
    if not errmsg=="":
        send_discord_notify(errmsg)
        return

    unique_papers = {p.entry_id: p for p in results if p.published >= base_date}
    filtered_papers = sorted(unique_papers.values(), key=lambda x: x.published, reverse=True)

    if not filtered_papers:
        date_str = now.strftime("%Y年%m月%d日")
        send_discord_notify(f"【{date_str}】本日は新着の論文はありませんでした。")
        return

    GOOGLE_API_KEY = os.environ.get('GEMINI_API_KEY')
    ai_client = genai.Client(api_key=GOOGLE_API_KEY)

    print(f"全 {len(filtered_papers)} 件を一括でAIに解析させます...")
    
    date_str = now.strftime("%Y年%m月%d日")
    final_output = f"**【本日({date_str})の新着論文: {len(filtered_papers)}件】**\n\n"
    
    all_papers_text = ""
    for idx, paper in enumerate(filtered_papers, 1):
        safe_title = clean_text(paper.title)
        safe_summary = clean_text(paper.summary)
        all_papers_text += f"\n--- 論文番号: {idx} ---\n【タイトル】{safe_title}\n【URL】{paper.entry_id}\n[アブスト]\n{safe_summary}\n"

    prompt = f"""
    私は重力波データ解析を研究している大学院生です。
    以下のようなテーマに興味を持っています。特に1番上は研究のテーマであり、特別な興味を持っています。
    ・重力波到来方向検出の高速化&厳密化
    ・上記に関する、パラメタ推定全般に関する高速化や仮定の吟味など
    ・確率的重力波背景放射の非ガウス的な解析を機械学習を用いて行う
    ・上記に関する、他の確率論的重力波背景放射のデータ解析のこと、重力波のデータ解析に機械学習を用いること
    ・ブラックホールの質量分布、階層進化の話
    ・ハッブルテンションを重力波から解く
    
    あなたは重力波やそれに関する物理学の専門家として、以下の論文リストを読み、各論文について5つを出力してください。
    1. 論文名とその和訳
    2. arxivリンク（この文はdiscordに転送します。押せばリンクに飛ぶようにしたいので、むき出しのURLだけをシンプルに貼ってください。）
    3. 和訳要約(3行程度)
    4. あなたの興味との関連度（1〜10点、太字で強調）、判定(7点以上なら「★ピックアップ」、それ以外は「スルー」)
    5. 点数の理由

		列挙する際は、点数順に、（低スコアのものも）すべて列挙してください。
    出力に関して、冒頭に「わかりました！」等書くことは不要です。レイアウトに沿って、わかりやすく、解説をお願いします。
    また、論文と論文の間の仕切りや点数は最大限強調して、その他も長文であることを考慮して見やすくレイアウトしてください。
    また、論文ごとに区切ってコードで解析を行うので、論文の紹介が終わって次の論文に移る際は、必ず「-----」と打ってください。逆に、それ以外の箇所で「-----」は使わないでください。
    
    [リスト]
    {all_papers_text}
    """

    print("--- AIによる一括解析を実行中... ---")
    gemini_wait_times = [
        30+random.random() * 5,
        60+random.random() * 10,
        90+random.random() * 15
    ]

    main_model = 'gemini-3.6-flash'
    backup_model = 'gemini-3.1-flash-lite'
    success = False
    
    for attempt, wait in enumerate(gemini_wait_times + [0]):
        try:
            print(f"[{main_model}] で解析を試みます... (試行 {attempt + 1})")
            response = ai_client.models.generate_content(
                model=main_model,
                contents=prompt,
            )
            final_output += response.text + "\n"
            print("AIによる解析が無事に完了しました！")
            success = True
            break

        except Exception as e:
            error_msg = str(e)
            # 503(混雑) または 429(制限) の場合は待機してリトライ
            if "503" in error_msg or "429" in error_msg:
                if wait > 0:
                    print(f"【混雑】Geminiサーバーが混雑中。{wait}秒待機して再試行します... (試行 {attempt + 1}/{len(gemini_wait_times)})")
                    time.sleep(wait)
                else:
                    print(f"【失敗】{main_model} のリトライ上限に達しました。予備モデルに移行します。")
            else:
                print(f"【エラー】想定外のエラーのためメインを断念します: {error_msg}")
                break
                
    if not success:
        print(f"🚨 メインモデル失敗：予備モデル [{backup_model}] に切り替えて最終試行を行います...")
        try:
            response = ai_client.models.generate_content(
                model=backup_model,
                contents=prompt,
            )
            final_output += f"⚠️【お知らせ】メインのAIモデルが混雑しているため、予備モデル({backup_model})で要約を作成しました。\n\n"
            final_output += response.text + "\n"
            print(f"【予備で成功】予備モデル {backup_model} で無事に解析に成功しました！")
            success = True
        except Exception as e:
            print(f"【失敗】予備モデルも全滅しました: {e}")
            final_output += f"\n【エラー】予備のAIモデルも混雑のため失敗しました。詳細: {e}\n"
                

    # ＝＝＝ ここから追加：Proモデルによる深掘り解析（フェーズ2） ＝＝＝
    print("--- Proモデルによる★ピックアップ論文の深掘りを開始します ---")
    
    # 1. Flashの出力結果から「★ピックアップ」判定された論文のIDを抽出する
    # "--- 論文番号:" ごとにテキストを分割し、そのブロックにピックアップが含まれるか判定
    blocks = final_output.split("-----")
    pickup_ids = []
    for block in blocks[1:]: 
        if "★ピックアップ" in block:
            # ブロック内からarxivのURL（例: arxiv.org/abs/2401.01234）を抽出
            match = re.search(r'arxiv\.org/abs/(\d+\.\d+)', block)
            if match:
                pickup_ids.append(match.group(1))
                
    # IDの重複排除
    pickup_ids = list(set(pickup_ids))
    print(f"★ピックアップされた論文ID: {pickup_ids}")
    
    if not pickup_ids:
        print("本日は★ピックアップされた論文はありませんでした。深掘りをスキップして終了します。")
    else:
        print(f"深掘り対象論文: {len(pickup_ids)} 件 ({', '.join(pickup_ids)})")
        
        # 2. 対象論文を1つずつダウンロードしてProモデルに読ませる
        for paper_id in pickup_ids:
            try:
                # 取得済みの論文データから対象のオブジェクトを探す
                paper = next((p for p in filtered_papers if paper_id in p.entry_id), None)
                if not paper:
                    continue
                    
                print(f"\n【深掘り開始】{paper_id} のPDFをダウンロードします...")
                pdf_path = f"https://arxiv.org/pdf/{paper_id}"
                paper.download_pdf(filename=pdf_path)
                
                print("GeminiにPDFをアップロード中...")
                uploaded_file = ai_client.files.upload(file=pdf_path)
                
                # サーバー側でPDFの処理が完了するのを少し待機
                time.sleep(5 + random.random())
                
                deep_prompt = f"""
                私は重力波データ解析を研究している大学院生です。
                この論文（{paper.title}）の全文を読み込み、以下の点について私の研究に役立つように詳細に抽出・要約してください。

                - 研究の目的と背景
                - 提案手法の具体的な数式展開やアルゴリズムの詳細
                - 実験設定やデータセットの詳細（特に重力波データ解析に関する部分）
                - 実験結果の具体的な数値やグラフの要約
                - サンプリング時の計算コスト（具体的な実行時間やリソースの削減具合）への言及はあるか
                - ノイズの非ガウス性をどのように取り扱っているか
                - この論文の新規性と、仮定している前提条件
                
                アブストラクトの繰り返しではなく、数式展開や実験セクション（Method/Results）から具体的な手法や数値を抜き出して解析してください。
                見やすくMarkdownの箇条書きや表を使って出力してください。
                """
                
                print(f"gemini-2.5-pro で全文解析を実行中...")
                deep_response = ai_client.models.generate_content(
                    model='gemini-2.5-pro',
                    contents=[uploaded_file, deep_prompt]
                )
                
                # 3. 成功したらDiscordへ個別に追撃通知
                deep_msg = f"**【深掘りレポート: {paper.title}】**\nURL: {paper.entry_id}\n\n{deep_response.text}"
                final_output += deep_msg
                send_discord_notify(final_output)
                print(f"{paper_id} の深掘り完了・通知しました。")
                
            except Exception as e:
                # 万が一エラーが起きてもスクリプト全体は止めず、Discordにエラーだけ通知して次の論文へ
                err_msg = f"⚠️ 【深掘りエラー】{paper_id} の解析中にエラーが発生しました（スキップします）: {e}"
                print(err_msg)
                final_output += err_msg
                send_discord_notify(final_output)
                
            finally:
                # 4. ゴミが残らないよう、成功しても失敗しても必ずファイルを削除
                try:
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                    if 'uploaded_file' in locals():
                        ai_client.files.delete(name=uploaded_file.name)
                except Exception as cleanup_e:
                    print(f"ファイルクリーンアップ失敗: {cleanup_e}")
                    

if __name__ == "__main__":
    fetch_and_summarize_papers()
