#!/usr/bin/env python3
"""UI U0.3: fetch the user-authorised game-asset sources for the local panel UI (never committed).

Only two kinds of network source are allowed:

* exactly the ten user-approved ``shinycolors.enza.fun/assets/<hash>?v=N`` static URLs the user hand-picked (pinned below with the
  sha256 of the bytes we first downloaded) — no other enza URL, no enumeration of hashes, no game client, no traffic;
* the public Shinycolors Wiki card lists ``https://shinycolors.wiki/wiki/Card_List/Support`` (S cards) and
  ``https://shinycolors.wiki/wiki/Card_List/Produce`` (P cards, for the Produce team picker),
  and the ``CardIcon-<10 digits>.png`` thumbnails they reference on ``image.shinycolors.wiki``;
* ShinyColorsDB (「缩略图用db的 因为是直接从游戏提取的」): only the read-only card
  enumeration ``https://api.shinycolors.moe/info/idolList`` + ``/info/idolInfo?idolId=<n>`` (its ``cardLists``), and
  the card icons ``https://cf-static.shinycolors.moe/images/content/idols/icon/1<9 digits>.png`` (P) and
  ``…/support_idols/icon/2<9 digits>.png`` (S), keyed by the exact 10-digit ``enzaId``. No card detail, skill or stat
  endpoint is requested (thumbnails only). The images are extracted from the game and carry no licence: local private
  cache only, never committed or redistributed. HTTP 429/403 stops the run (resume later; never hammered).

Rules: robots.txt is checked for every wiki host, at most one request per second, no cookies (plain ``urllib`` opener
without a cookie processor), redirects only inside the allowlist, a plain identifying User-Agent, and a total download
cap. Everything is written under ``dev_assets/private/game-assets-src/`` (gitignored; checked before writing).
``tools/ui_game_assets.py`` then builds ``ui/game-assets/`` offline from these files.

Usage::

    python tools/ui_game_assets_fetch.py --check        # show what is present; no network
    python tools/ui_game_assets_fetch.py --fetch        # enza atlases + pinned tiles (skips ones already present and verified)
    python tools/ui_game_assets_fetch.py --fetch --wiki # also the wiki S + P card lists and thumbnails (resumable)
    python tools/ui_game_assets_fetch.py --fetch --wiki --skip-enza --lists produce   # only the P list + thumbnails
    python tools/ui_game_assets_fetch.py --fetch --db --skip-enza   # ShinyColorsDB P + S card icons (resumable)
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from tools.local_output import portable_output_allowed

SRC_DIR = ROOT / "dev_assets" / "private" / "game-assets-src"
WIKI_DIR = SRC_DIR / "wiki-support"
USER_AGENT = "MaaShinyColors-local-thumbnails/0.1 (personal local UI; 1 req/s)"
MIN_INTERVAL = 1.1  # seconds between requests (<= 1 request per second)
# 2026-11-23 (user approved): the static card-icon CDN gets 4 parallel workers and >= 0.2 s between request starts
# (about 5/s; the API and wiki hosts keep 1/s). 429/403 still stops the run.
CDN_INTERVAL = 0.2
ICON_WORKERS = 4
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
TIMEOUT = 60

# User hand-picked (enza-1..4) and user-approved (enza-5..8, 2026-10-04; enza-9 and enza-10, 2026-10-08) enza static assets. url -> pinned bytes; nothing else on enza.fun is ever requested.
ENZA_SOURCES = (
    {"id": "enza-1", "url": "https://shinycolors.enza.fun/assets/7f697481939646e7371fd37596e0055b265b229c4f94dffac38bf51b87fce6a4?v=49",
     "sha256": "dadb693ae4691d05c5a70896517d4ffc4b2a8d9809bf2251ea32c59964baaf98", "path": "enza-1.webp",
     "purpose": "主页 UI 图集 1085×1085：マニー/ジュエル 顶栏小图标、プレゼント/ミッション/ショップ/お知らせ/営業/ガシャ/フェス 按钮"},
    {"id": "enza-2", "url": "https://shinycolors.enza.fun/assets/50989a84739aa52800afb56fb9cac1330207c874390b256ca2acde5e4bd74c75?v=46",
     "sha256": "977ea1beaf5ec44b3ce09494cbc9a1a8d457ef1bd93f723d13273cb45e1fdaf4", "path": "enza-2.webp",
     "purpose": "道具图集 1326×1326：150×150 道具格（マニー星币、ジュエル、フェスP 等）与各种小图标"},
    {"id": "enza-3", "url": "https://shinycolors.enza.fun/assets/7d84382d6ef3a1e5580d739ea856f79a5c24b0bfb596224b655ce89fb0c255e1?v=7",
     "sha256": "ffcb679efdaaef05d8325bb10ab9c737ca6af1ee7d76be7db51d3cd3cd894438", "path": "enza-3.webp",
     "purpose": "白金训练券（PLATINUM TICKET）道具格 148×148"},
    {"id": "enza-4", "url": "https://shinycolors.enza.fun/assets/33c9066ff372eee1ca7d09078701994b87fa93f7516540b5e53456fe222bfc5d?v=8",
     "sha256": "32b1d5ad3998120a699cc51251cf158d98face515e91006b0c04285a272caf25", "path": "enza-4.webp",
     "purpose": "金训练券（GOLD TICKET）道具格 148×148"},
    {"id": "enza-5", "url": "https://shinycolors.enza.fun/assets/7221d3d1d9e0bbb01a3e8419c5b98964887bb0d43ef2d8a930787b95278273c9?v=1",
     "sha256": "473bbb316060d1476ab3ef031795dd3f942b1ff089c8c56b615762adcd7bd0cd", "path": "enza-5.webp",
     "purpose": "跳过券（SKIP TICKET）道具格 148×148（2026-10-04 用户批准）"},
    {"id": "enza-6", "url": "https://shinycolors.enza.fun/assets/357163b7627d22e35d95707232d0c192ed1c39dbae3865ea9d4165aa52372c74?v=8",
     "sha256": "d7939aa1f98724267648857b95b7e35e7cad6780750d2879dd2cd01009099a84", "path": "enza-6.webp",
     "purpose": "铜训练券（BRONZE TICKET）道具格 148×148（2026-10-04 用户批准）"},
    {"id": "enza-7", "url": "https://shinycolors.enza.fun/assets/f6ec49880ff401feaa5f1304968651a30027eea0ba37f7818d11d54cf6aee8e6?v=8",
     "sha256": "1fffc3c801dffdf13262fa1cfea06bb2eb7dafe04f21d5b23d30a34410aaa13f", "path": "enza-7.webp",
     "purpose": "银训练券（SILVER TICKET）道具格 148×148（2026-10-04 用户批准）"},
    {"id": "enza-8", "url": "https://shinycolors.enza.fun/assets/4941788029c750a003577f19d591e67d1d17bb56d8b8ce29cdcf91695b4d3cb8?v=3",
     "sha256": "97b9e47f5b82eff3cf032c3f6ec5ab126a967ee57479eaa6132a6652d8587fc4", "path": "enza-8.webp",
     "purpose": "Lv50 训练券道具格 148×148（2026-10-04 用户批准）"},
    {"id": "enza-9", "url": "https://shinycolors.enza.fun/assets/838bc741063799df09eef6f032f97704eb6b8932ec45ea53de3690da5b54a721?v=9",
     "sha256": "0803256e1ef68ff7751a997d53a53537f396f25e1be169981b684484dae9d7c3", "path": "enza-9.webp",
     "purpose": "Produce 评价徽章图集 340×340：SS/S+/S/A/B/C/D/E/F（2026-10-08 用户批准，用于 Produce 结束确认的总合评价）"},
    {"id": "enza-10", "url": "https://shinycolors.enza.fun/assets/8f5a4652a6d1d7a160fa53c7c06f245463066358eaf592a4be18e5a7a634f24a?v=85",
     "sha256": "0df8580f016ca973c6b03a37da83a3546d726b0096e708a30d6337a9954d6f98", "path": "enza-10.webp",
     "purpose": "UI 按钮/标签图集 1607×1607：取ホーム（房子）、オプション（工具）、アイテム一覧（罐）三个单色字形作标签页与任务图标（2026-10-08 用户批准）"},
)
ENZA_URLS = frozenset(s["url"] for s in ENZA_SOURCES)
# 2026-10-08c: single 148x148 item tiles the user copied from their own browser network list (dev_assets/private/game-assets-src/
# chrome-urls.txt, 「我已保存到chrome-urls.txt请读取并匹配」). Only the tiles the UI uses are pinned (exact URL + sha256 of the
# raw response, fetched cookie-less); they were matched to the 资源→プロデュースアイテム screenshot crops by template
# correlation and confirmed visually. Stored as enza-tiles/<key>.webp.
# 2026-10-08d: purposes carry the in-game names read from アイテム一覧 (read-only tab taps / scrolling);
# お手製差し入れ added (消費アイテム tile = c048e6731773). Full private name<->tile list: chrome/catalog-named.json.
ENZA_TILES = (
    {"key": "998826b0739d", "item": "fes_ticket", "url": "https://shinycolors.enza.fun/assets/998826b0739dea166cf4baf7f1b022c706b551c628834488da0a629c14420b08?v=8",
     "sha256": "8a8cd9e716f7ff2c65103698f65888945c95d5f883f18476dfdb49e792ca17b8", "purpose": "フェスエントリーチケット（白色 VS FES 券；粉色 3ac48f5eb96e 为「フェスエントリーチケット5」，アイテム一覧 消費アイテム 2026-10-08）"},
    {"key": "c4b5ec35a4e6", "item": "stamina_drink", "url": "https://shinycolors.enza.fun/assets/c4b5ec35a4e61e309b0d5de78bb051f40ab95fa97885793d33307d6a4cb740d5?v=8",
     "sha256": "cfc27462f2b914ce98786f5670023642d88b7cf21d7538a2877dc859bebc0c41", "purpose": "リカバリーソーダ30（蓝；60 绿 b9cd6d871d04 / 100 红 c94d5d69344f，アイテム一覧 消費アイテム 2026-10-08）"},
    # 2026-10-15: soda 60/100 (same 消費アイテム row; resource page showed grey plates for them).
    {"key": "b9cd6d871d04", "item": "stamina_drink_60", "url": "https://shinycolors.enza.fun/assets/b9cd6d871d047a5138a971acd6aa7a6708a89696d29a88f286cad3ae1e28932b?v=8",
     "sha256": "f93fe6072d805bb5fb09aed1983e008db7d86de7676392614db194642c358709", "purpose": "リカバリーソーダ60（绿）"},
    {"key": "c94d5d69344f", "item": "stamina_drink_100", "url": "https://shinycolors.enza.fun/assets/c94d5d69344fc890adca43ee78150c5d420d82a5dfc8311ed1dff972af4fdce7?v=8",
     "sha256": "0aea5a347a6e998c8d335f241e6bf00aa3bc04a43a51ae33468ae122a1820889", "purpose": "リカバリーソーダ100（红）"},
    {"key": "f7cbdb97f803", "item": "p_tart_yellow_3", "url": "https://shinycolors.enza.fun/assets/f7cbdb97f80393650e8452859354b0aff57a59ba1d240ffe9d1c229d56c64975?v=9",
     "sha256": "cf7a043dbd37060ec45335b0da129ee1d587b043676d3d2d0dceab31529509c1", "purpose": "ヒーリングタルト(3個入り)"},
    {"key": "280c52957807", "item": "p_tart_blue_2", "url": "https://shinycolors.enza.fun/assets/280c529578074aa9e87be670e75c40758cae8f6925519d6341ecf9a68a3b3f1a?v=9",
     "sha256": "e2d201f423c025e0ca8539d8d1d9e6b6fa42db31ed3fada7ef206c96272f5755", "purpose": "ヒーリングフルーツタルト(2個入り)"},
    {"key": "392339946b42", "item": "p_tart_blue_3", "url": "https://shinycolors.enza.fun/assets/392339946b42935cf1f688bf89e7bdbe8b82c1d74a5daf9c23bca7eeed8eb006?v=9",
     "sha256": "98d8adad01f7a46005f5a84d83a4743fc485c3e9a4a8236aa0a0fa4427ddb2be", "purpose": "ヒーリングフルーツタルト(3個入り)"},
    {"key": "697f782096d4", "item": "p_tart_black_2", "url": "https://shinycolors.enza.fun/assets/697f782096d4b7cc2d72390fc71097112b3f647a314104ba73a1afd43ace0f80?v=9",
     "sha256": "983a9484793fc5da9b605cbb87afe401c6e1d49bf73bd6caeffa2156270d8efa", "purpose": "高級ヒーリングフルーツタルト(2個入り)"},
    {"key": "da0b3b56e4c7", "item": "p_tart_black_3", "url": "https://shinycolors.enza.fun/assets/da0b3b56e4c77be7773f698c0df329999351f31606d5299aba79cd98a9d1bb55?v=9",
     "sha256": "2442ec5fcecebdcdeeefeb28176185568024c247de894cc8076635d1560fa45a", "purpose": "高級ヒーリングフルーツタルト(3個入り)"},
    {"key": "9e95a17da4ba", "item": "p_candle_1", "url": "https://shinycolors.enza.fun/assets/9e95a17da4ba78a2fe5b029965fe9cc4fdc19291610090f5f50e1961008484e4?v=9",
     "sha256": "f561e88f52252a36978f28920271808e4420e1c66a80cc24be8861c60589dbcc", "purpose": "プチテンションアロマ"},
    {"key": "f26ecebddc8c", "item": "p_candle_2", "url": "https://shinycolors.enza.fun/assets/f26ecebddc8c573750ab71b0cb7760ae4a5180fc67bbba46bc4d4dc5299168a5?v=9",
     "sha256": "cebf64f543c3c36f0b660a91932b1b320d46fe466db7898adabc74e922c7f7d4", "purpose": "プチテンションアロマ(2個入り)"},
    {"key": "f39e7ac1c7d3", "item": "p_candle_3", "url": "https://shinycolors.enza.fun/assets/f39e7ac1c7d360367c04221f7f29d638894a45d743e2d404103fdb17b6be06e0?v=9",
     "sha256": "735100e34cb6568092d57448b296acdfee04bc333645fee975c39b79c98afad3", "purpose": "プチテンションアロマ(3個入り)"},
    {"key": "c48dcd349ed5", "item": "p_magazine_1", "url": "https://shinycolors.enza.fun/assets/c48dcd349ed587d5db43eba00fd6161cfe2b0dbe65124a2f350dbf5ab1eaf5f0?v=9",
     "sha256": "3510171454c8642b8765de6eaf2c9501e4992d3e4296b79d16e43635d93e1c25", "purpose": "流行雑誌"},
    {"key": "f087c86d5e17", "item": "p_magazine_2", "url": "https://shinycolors.enza.fun/assets/f087c86d5e1755a98fcf50b39bb6ec3790e82b8b4bb179cabba3b25793ed8125?v=5",
     "sha256": "d02775b98e53e2450ceac51245d06a8476287199f6db7495cce3cbf269952e28", "purpose": "流行雑誌(2冊入り)"},
    {"key": "2e7beab43a2c", "item": "p_magazine_dance", "url": "https://shinycolors.enza.fun/assets/2e7beab43a2c01b13568b965e5df47060b43b6ea5a4d61337491170f78ac4eb3?v=4",
     "sha256": "7833d57a3f30dc7fd8fb3a55769df24079433d4263a04e86b401e8aef4991dc0", "purpose": "Dance流行雑誌"},
    {"key": "1b81fa57a0dd", "item": "p_magazine_vocal", "url": "https://shinycolors.enza.fun/assets/1b81fa57a0dd58d931e11778070cffb8bdca2a4c7d118050f51dd180bbb309da?v=4",
     "sha256": "285e22d542044af93a44cd27d947319107b4ce82658cadd17998f78fe2769a68", "purpose": "Vocal流行雑誌"},
    {"key": "feb0924622ab", "item": "p_magazine_visual", "url": "https://shinycolors.enza.fun/assets/feb0924622ab4c4cca837fe43bf9044a8c9189615378d7af57ed78f691c83120?v=4",
     "sha256": "7dda2e0f52191c73b4db6875745388a3b3275218af47d4d8fdfca01f25dc1206", "purpose": "Visual流行雑誌"},
    {"key": "eb9172cc63d5", "item": "p_secret_memo", "url": "https://shinycolors.enza.fun/assets/eb9172cc63d50d1f6b6c656064e0b6030f01cac901b703cdb6a9bf387a63e5c0?v=14",
     "sha256": "a2833efc8eb6b60ab47b7d605866022bb150c0a5207a732d09b319cd35ebefea", "purpose": "秘密のメモ帳"},
    {"key": "f0abcd6cf2bf", "item": "p_bookmark_orange", "url": "https://shinycolors.enza.fun/assets/f0abcd6cf2bf4e4223c8b6b1daac7bcde37b638bd1c7ffaf57b7c6510448ea89?v=2",
     "sha256": "9ad1677c0509c4a4f242b1a8149ec414f6248978deb7a34f68cbe2aac72e902c", "purpose": "ノウハウのしおり(1葉)"},
    {"key": "107eae981152", "item": "p_bookmark_rainbow", "url": "https://shinycolors.enza.fun/assets/107eae981152b6f90685ded4c61e6f75596dfbc217da7a0c25d2873e5b59ac72?v=2",
     "sha256": "91759b627fa325032a2597bf1f41d7481d1f9b818a813b8a2fcc415a4cb2c22c", "purpose": "高レベルノウハウのしおり(1葉)"},
    {"key": "284fbdc724f1", "item": "p_charm_pouch", "url": "https://shinycolors.enza.fun/assets/284fbdc724f1af2b736254fa23575706494cf8f14d87161745cb50eddd21b72b?v=4",
     "sha256": "b105929ba09151d0a2243eae8ea7006eaa58bdc2d3be2526f016a3acda7ba673", "purpose": "親愛のお守り"},
    {"key": "7b14b782ebd4", "item": "p_book_magnifier", "url": "https://shinycolors.enza.fun/assets/7b14b782ebd400d3efc7daa7a080ef3fb2c876b050187731bfdbfd99df3c1010?v=2",
     "sha256": "8d7cfd2f4f083bc157c773a1f9d3698b6052e782b583260800b3f99e7ed33aed", "purpose": "ノウハウ目録"},
    {"key": "c048e6731773", "item": "handmade_gift", "url": "https://shinycolors.enza.fun/assets/c048e6731773ba1b2810ae3e6aeea99fa683a181cce6e53c275a2d99195f0f77?v=4",
     "sha256": "732c667368e31860a461695f84c16f7f0322cee82c0da8e7126d05a1f16ba3d4", "purpose": "お手製差し入れ（营业差し入れ；アイテム一覧 消費アイテム 2026-10-08）"},
    # 2026-10-09: 商店「其他商品」 day goods with a named tile in chrome/catalog-named.json.
    {"key": "51c3bc646558", "item": "p_morning_bread", "url": "https://shinycolors.enza.fun/assets/51c3bc6465584845ae07dedc293aeba36218afb1b533911c78da24b091546488?v=8",
     "sha256": "3b617737c89e7682b1e04c0db1ee4db816616b14f71681c1ff97032e8c307350", "purpose": "モーニングコミュパン（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "aa458caccfc5", "item": "p_toilette", "url": "https://shinycolors.enza.fun/assets/aa458caccfc5bbd6bae29f49c21b151f47e3bc9b0cf5c17609ff9d0dff528c50?v=9",
     "sha256": "557451fa912c4341f4bc832c3d8aca104351c579f217f0cbcddbb55f7fc340eb", "purpose": "約束のトワレ（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "0141d95ffd0a", "item": "p_cologne", "url": "https://shinycolors.enza.fun/assets/0141d95ffd0a802479a2bb6d872880683f4c400872e752907a570a7a69193922?v=9",
     "sha256": "6d7df5c9bb1a830bf44480147a60f35b9c7b65027225529830fb7c3773676721", "purpose": "約束のコロン（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "897b733209d6", "item": "p_book_visual_basic", "url": "https://shinycolors.enza.fun/assets/897b733209d67b9c99d1d415fc206772c12845827fd6f41ff9532e5eea60bd49?v=8",
     "sha256": "9de188327f44a50a65912bc209c3a4dd8139f7714f5e047f1f26a1a777b380bb", "purpose": "ビジュアル基礎知識本（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "a61aa923e84f", "item": "p_trouble_charm", "url": "https://shinycolors.enza.fun/assets/a61aa923e84f2e52f43f7141adf0c50935f7d4ca55ddb923e816626e4d6c4bfd?v=9",
     "sha256": "e85b8d2764a4a1affae087d42eb80aa14dc7a31a58017569c3b36e1c9b47119c", "purpose": "トラブル守り（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "ae4799618217", "item": "p_aroma_2", "url": "https://shinycolors.enza.fun/assets/ae47996182174a581f80036e9fb7f381a0574e9c50ea3a95a113c9a2d625ad17?v=9",
     "sha256": "66fd4f5066159fc77bbbab0e1d513906d3ab83a77f14614636307dfe1aa54f60", "purpose": "テンションアロマ(2個入り)（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "533155e89e0e", "item": "p_tart_yellow_2", "url": "https://shinycolors.enza.fun/assets/533155e89e0e79ad7d6dbcd437a63f4b4fa31bd82efb9e7c82e8bad4f43c0424?v=9",
     "sha256": "2d4d950cdf33aadcd3ee6091d1a9fdab32805d08bf50d0b731feefb7e6c0d3f4", "purpose": "ヒーリングタルト(2個入り)（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "1451c9d20299", "item": "p_schedule_note", "url": "https://shinycolors.enza.fun/assets/1451c9d20299f76b4238946b48cf27e4f0614f0510dda3d2caa2a834c6986ebf?v=9",
     "sha256": "c5c39e4a6430c87c1ed89fe5257d86c1a04fc17b3ae4bf820d8464f0de664bec", "purpose": "予定変更付箋（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
    {"key": "9f0c013f3640", "item": "towel_cometik", "url": "https://shinycolors.enza.fun/assets/9f0c013f36407a956e2248155078918c7322adf5ad8f28ee308a414bfb46b8a4?v=1",
     "sha256": "26690d0cf47fa248732e5760f5030afe6b73f0c17e2b7446c4903f5f084cb919", "purpose": "コメティックのタオル（商店本日限定；アイテム一覧 プロデュースアイテム 2026-10-08d 对照，2026-10-09 目视确认）"},
)
ENZA_TILE_URLS = frozenset(t["url"] for t in ENZA_TILES)
TILE_DIR = SRC_DIR / "enza-tiles"


def decoded_tile_file(key):
    """Decoded copy (chrome/<12 hex>.png) saved when the user's アイテム一覧 URLs were catalogued; its sibling
    chrome/<key>.json records the raw bytes' sha256. Used only when the raw tile was not fetched (no network needed)."""
    return SRC_DIR / "chrome" / f"{key}.png"


def tile_file(key):
    """Local path of a pinned tile (enza-tiles/<12 hex>.webp)."""
    return TILE_DIR / f"{key}.webp"
WIKI_HOSTS = frozenset({"shinycolors.wiki", "image.shinycolors.wiki"})
WIKI_LIST_URL = "https://shinycolors.wiki/wiki/Card_List/Support"
WIKI_LIST_FILE = WIKI_DIR / "Card_List_Support.html"
WIKI_CARDS_FILE = WIKI_DIR / "cards.json"
WIKI_ICON_DIR = WIKI_DIR / "icons"
# P cards (Produce idols): same wiki, same CardIcon pattern, kept in their own folder.
WIKI_P_DIR = SRC_DIR / "wiki-produce"
WIKI_P_LIST_URL = "https://shinycolors.wiki/wiki/Card_List/Produce"
WIKI_P_LIST_FILE = WIKI_P_DIR / "Card_List_Produce.html"
WIKI_P_CARDS_FILE = WIKI_P_DIR / "cards.json"
WIKI_P_ICON_DIR = WIKI_P_DIR / "icons"
# kind -> (list URL, saved page, parsed cards.json, icon folder, caption prefix)
WIKI_LISTS = {
    "support": (WIKI_LIST_URL, WIKI_LIST_FILE, WIKI_CARDS_FILE, WIKI_ICON_DIR, "S"),
    "produce": (WIKI_P_LIST_URL, WIKI_P_LIST_FILE, WIKI_P_CARDS_FILE, WIKI_P_ICON_DIR, "P"),
}
_WIKI_PATHS = (
    re.compile(r"^/robots\.txt$"),
    re.compile(r"^/wiki/Card_List/(?:Support|Produce)$"),
    re.compile(r"^/(?:[0-9a-f]/[0-9a-f]{2}/)CardIcon-\d{10}\.png$"),
)
RARITIES = ("UR", "SSR", "SR", "R", "N")

# ShinyColorsDB (shinycolors.moe): enumeration API + game-extracted card icons. Thumbnails only.
DB_API_HOST = "api.shinycolors.moe"
DB_CDN_HOST = "cf-static.shinycolors.moe"
DB_HOSTS = frozenset({DB_API_HOST, DB_CDN_HOST})
DB_DIR = SRC_DIR / "shinycolorsdb"
DB_IDOLS_DIR = DB_DIR / "idols"
DB_ICON_DIR = DB_DIR / "icons"
DB_CARDS_FILE = DB_DIR / "cards.json"
DB_IDOL_LIST_URL = f"https://{DB_API_HOST}/info/idolList"
DB_MAX_TOTAL_BYTES = 40 * 1024 * 1024
DB_MAX_IDOLS = 200
DB_CARD_ID = re.compile(r"^[12]\d{9}$")
DB_CARD_TYPE = re.compile(r"^([PS])_(UR|SSR|SR|R|N)$")
_DB_ICON_PATHS = {"1": "idols", "2": "support_idols"}
_DB_API_PATHS = re.compile(r"^/(?:robots\.txt|info/idolList|info/idolInfo)$")
_DB_IDOL_QUERY = re.compile(r"^idolId=\d{1,4}$")
_DB_CDN_PATHS = re.compile(r"^/(?:robots\.txt|images/content/idols/icon/1\d{9}\.png|images/content/support_idols/icon/2\d{9}\.png)$")


class RateLimited(Exception):
    """HTTP 429 / 403 from a source: stop the run instead of retrying."""


def db_icon_url(card_id):
    """CDN icon URL for an exact 10-digit card id (1… P -> idols/icon, 2… S -> support_idols/icon), else None."""
    if not isinstance(card_id, str) or not DB_CARD_ID.match(card_id):
        return None
    return f"https://{DB_CDN_HOST}/images/content/{_DB_ICON_PATHS[card_id[0]]}/icon/{card_id}.png"


def db_idol_url(idol_id):
    return f"https://{DB_API_HOST}/info/idolInfo?idolId={int(idol_id)}"


def _db_allowed(parts):
    if parts.fragment:
        return False
    if parts.hostname == DB_API_HOST:
        if not _DB_API_PATHS.match(parts.path):
            return False
        if parts.path == "/info/idolInfo":
            return bool(_DB_IDOL_QUERY.match(parts.query))
        return not parts.query
    return not parts.query and bool(_DB_CDN_PATHS.match(parts.path))


def is_allowed_url(url):
    """True only for the ten pinned enza atlases and the pinned single item tiles (ENZA_TILES), the two wiki list pages / robots.txt / CardIcon thumbnails, and the
    ShinyColorsDB idol list / per-idol card list / P+S card icons (exact patterns, see the module docstring)."""
    if url in ENZA_URLS or url in ENZA_TILE_URLS:
        return True
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or port or parts.username or parts.password:
        return False
    if parts.hostname in DB_HOSTS and parts.netloc == parts.hostname:
        return _db_allowed(parts)
    if parts.hostname not in WIKI_HOSTS:
        return False
    if parts.query or parts.fragment:
        return False
    if parts.hostname == "shinycolors.wiki":
        return bool(_WIKI_PATHS[0].match(parts.path) or _WIKI_PATHS[1].match(parts.path))
    return bool(_WIKI_PATHS[0].match(parts.path) or _WIKI_PATHS[2].match(parts.path))


def is_gitignored(path):
    # 2026-11-22: the release package has no .git; like ui_game_assets.py, allow its private/output folders there
    # (a new install had saved no icon at all, so the owned-card scan and the 资源 page had nothing to show)
    from tools.local_output import portable_output_allowed
    if portable_output_allowed(Path(path), ROOT):
        return True
    try:
        result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


class _AllowlistRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_url(newurl):
            raise urllib.error.HTTPError(newurl, code, f"redirect outside allowlist: {newurl}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    """Rate-limited, cookie-less, allowlisted GET with a running byte budget."""

    def __init__(self, max_total=MAX_TOTAL_BYTES):
        # No HTTPCookieProcessor: cookies are neither stored nor sent.
        self.opener = urllib.request.build_opener(_AllowlistRedirect())
        self.opener.addheaders = [("User-Agent", USER_AGENT)]
        self.max_total = max_total
        self.total = 0
        self.requests = 0
        self._last = {}            # host -> next permitted request start (monotonic)
        self._robots = {}
        self._lock = threading.Lock()

    def _wait(self, url=""):
        host = urllib.parse.urlsplit(url).hostname or ""
        interval = CDN_INTERVAL if host == DB_CDN_HOST else MIN_INTERVAL
        with self._lock:
            now = time.monotonic()
            start = max(now, self._last.get(host, 0.0))
            self._last[host] = start + interval
            self.requests += 1
        if start > now:
            time.sleep(start - now)

    def _raw(self, url):
        if not is_allowed_url(url):
            raise ValueError(f"URL 不在允许列表中: {url}")
        self._wait(url)
        try:
            with self.opener.open(urllib.request.Request(url, headers={"Accept": "*/*"}), timeout=TIMEOUT) as response:
                data = response.read(MAX_FILE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code in (429, 403) and not url.endswith("/robots.txt"):
                raise RateLimited(f"HTTP {error.code}（{error.headers.get('Retry-After') or '无 Retry-After'}）: {url}") from error
            raise
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"单个文件超过 {MAX_FILE_BYTES} 字节: {url}")
        return data

    def robots_ok(self, url):
        host = urllib.parse.urlsplit(url).hostname
        if host not in WIKI_HOSTS | DB_HOSTS:
            return True  # enza URLs are user hand-picked single files, not crawled
        if host not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                parser.parse(self._raw(f"https://{host}/robots.txt").decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as error:
                parser.parse([] if error.code in (404, 410) else ["User-agent: *", "Disallow: /"])
            self._robots[host] = parser
        return self._robots[host].can_fetch(USER_AGENT, url)

    def get(self, url):
        if not self.robots_ok(url):
            raise PermissionError(f"robots.txt 不允许: {url}")
        data = self._raw(url)
        with self._lock:
            if self.total + len(data) > self.max_total:
                raise OverflowError(f"超过总下载上限 {self.max_total // (1024 * 1024)} MB")
            self.total += len(data)
        return data


def _write(path, data):
    path = Path(path)
    if not portable_output_allowed(path, ROOT) and not is_gitignored(path):
        raise ValueError(f"{path} 不在允许的本地素材目录，拒绝写入")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch_enza(fetcher, force=False):
    report = []
    for source in ENZA_SOURCES:
        target = SRC_DIR / source["path"]
        if target.is_file() and sha256_bytes(target.read_bytes()) == source["sha256"] and not force:
            report.append({"id": source["id"], "status": "present"})
            continue
        data = fetcher.get(source["url"])
        digest = sha256_bytes(data)
        if digest != source["sha256"]:
            # The asset changed upstream: keep the new bytes aside for review, never overwrite the pinned file silently.
            _write(target.with_name(target.stem + ".changed" + target.suffix), data)
            report.append({"id": source["id"], "status": "sha256_changed", "sha256": digest, "bytes": len(data)})
            continue
        _write(target, data)
        report.append({"id": source["id"], "status": "downloaded", "bytes": len(data)})
    return report


def fetch_tiles(fetcher, force=False):
    """The pinned single item tiles; same rules as fetch_enza (a changed upstream file is kept aside, never used)."""
    report = []
    for tile in ENZA_TILES:
        target = tile_file(tile["key"])
        if target.is_file() and sha256_bytes(target.read_bytes()) == tile["sha256"] and not force:
            report.append({"key": tile["key"], "status": "present"})
            continue
        data = fetcher.get(tile["url"])
        digest = sha256_bytes(data)
        if digest != tile["sha256"]:
            _write(target.with_name(target.stem + ".changed" + target.suffix), data)
            report.append({"key": tile["key"], "status": "sha256_changed", "sha256": digest, "bytes": len(data)})
            continue
        _write(target, data)
        report.append({"key": tile["key"], "status": "downloaded", "bytes": len(data)})
    return report


def _text(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


# Wiki column header -> our field. Unknown headers are ignored; the Icon column is located by header too.
_COLUMNS = {"icon": "icon", "character": "idol_en", "title": "title_en", "jp title": "jp", "how to acquire": "acquire",
            "specialty": "specialty", "idea": "idea", "fes": "fes"}
_FALLBACK_COLUMNS = {"icon": 0, "idol_en": 1, "title_en": 2, "jp": 3, "acquire": 4, "specialty": 5, "idea": 6}


def _header_map(body):
    """{field: column index} from the header row of a wiki table, or {} when the table has none."""
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body, flags=re.S):
        heads = re.findall(r"<th[^>]*>(.*?)</th>", row, flags=re.S)
        if heads:
            names = [" ".join(_text(cell).lower().split()) for cell in heads]
            return {_COLUMNS[name]: i for i, name in enumerate(names) if name in _COLUMNS}
    return {}


def parse_card_list(page, prefix="S"):
    """Parse a wiki 'Card List/Support' (prefix S) or 'Card List/Produce' (prefix P) page ->
    [{card_id, rarity, idol_en, title_en, idol, title, acquire, specialty, idea, icon_url}]. Columns are located by
    their header names (the P list has an extra 'Fes' column); tables without a header row use the Support order."""
    cards, seen = [], set()
    for section in re.split(r"<caption>", page)[1:]:
        caption = re.match(rf"\s*{prefix}-(UR|SSR|SR|R|N)\s*</caption>", section)
        if not caption:
            continue
        rarity = caption.group(1)
        body = section.split("</table>", 1)[0]
        cols = _header_map(body)
        cols = cols if "icon" in cols and "jp" in cols else _FALLBACK_COLUMNS
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body, flags=re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
            if len(cells) <= max(cols["icon"], cols["jp"]):
                continue
            icon = re.search(r'<img[^>]+src="(https://image\.shinycolors\.wiki/[0-9a-f]/[0-9a-f]{2}/CardIcon-(\d{10})\.png)"',
                             cells[cols["icon"]])
            if not icon:
                continue
            url, card_id = icon.groups()
            if card_id in seen or not is_allowed_url(url):
                continue
            seen.add(card_id)

            def cell(field):
                i = cols.get(field)
                return " ".join(_text(cells[i]).split()) if i is not None and i < len(cells) else ""

            jp = _text(cells[cols["jp"]]).split("\n")
            jp_title = re.match(r"^【(.+)】$", jp[0].strip()) if jp else None
            idea = ""
            if cols.get("idea") is not None and cols["idea"] < len(cells):
                found = re.search(r'title="(Vocal|Dance|Visual|Mental)"', cells[cols["idea"]])
                idea = found.group(1) if found else ""
            cards.append({
                "card_id": card_id, "rarity": rarity, "idol_en": cell("idol_en"), "title_en": cell("title_en"),
                "title": jp_title.group(1) if jp_title else jp[0].strip(), "idol": jp[-1].strip() if len(jp) > 1 else "",
                "acquire": cell("acquire"), "specialty": cell("specialty"), "idea": idea, "icon_url": url,
            })
    return cards


def parse_support_list(page):
    """Parse the wiki 'Card List/Support' tables (S cards)."""
    return parse_card_list(page, "S")


def parse_produce_list(page):
    """Parse the wiki 'Card List/Produce' tables (P cards)."""
    return parse_card_list(page, "P")


def fetch_wiki(fetcher, refresh_list=False, kind="support"):
    url, list_file, cards_file, icon_dir, prefix = WIKI_LISTS[kind]
    report = {"kind": kind, "list": None, "cards": 0, "downloaded": 0, "present": 0, "failed": [], "stopped": None}
    if list_file.is_file() and not refresh_list:
        page = list_file.read_text(encoding="utf-8")
        report["list"] = "present"
    else:
        data = fetcher.get(url)
        _write(list_file, data)
        page = data.decode("utf-8")
        report["list"] = "downloaded"
    cards = parse_card_list(page, prefix)
    report["cards"] = len(cards)
    for card in cards:
        target = icon_dir / f"CardIcon-{card['card_id']}.png"
        card["file"] = target.relative_to(SRC_DIR).as_posix()
        if target.is_file() and target.stat().st_size > 0:
            report["present"] += 1
            continue
        try:
            data = fetcher.get(card["icon_url"])
        except OverflowError as error:
            report["stopped"] = str(error)
            break
        except (urllib.error.URLError, OSError, ValueError, PermissionError) as error:
            report["failed"].append({"card_id": card["card_id"], "error": str(error)[:200]})
            continue
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            report["failed"].append({"card_id": card["card_id"], "error": "不是 PNG"})
            continue
        _write(target, data)
        report["downloaded"] += 1
    _write(cards_file, (json.dumps({"source": url, "kind": kind, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "cards": cards}, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return report


def parse_db_card(raw, idol_id=None):
    """One ShinyColorsDB ``cardLists`` entry -> our card record, or None. Exact ids only: ``enzaId`` must be a
    10-digit 1…/2… id whose prefix agrees with ``cardType`` (P_/S_ + rarity). No name matching; no skill/stat fields."""
    if not isinstance(raw, dict):
        return None
    card_id = raw.get("enzaId")
    kind = DB_CARD_TYPE.match(str(raw.get("cardType", "")))
    if not isinstance(card_id, str) or not DB_CARD_ID.match(card_id) or not kind:
        return None
    if {"P": "1", "S": "2"}[kind.group(1)] != card_id[0]:
        return None
    name = str(raw.get("cardName", ""))
    titled = re.match(r"^【(.+)】(.*)$", name)
    return {"card_id": card_id, "kind": kind.group(1), "rarity": kind.group(2),
            "title": titled.group(1) if titled else "", "idol": (titled.group(2) if titled else name).strip()[:40],
            "idol_id": raw.get("idolId", idol_id), "card_hash": str(raw.get("cardHash", ""))[:64],
            "release_date": str(raw.get("releaseDate", ""))[:10], "icon_url": db_icon_url(card_id)}


def image_format(data):
    """'png' / 'webp' by magic bytes (the CDN serves some ``.png`` icons as WebP), else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def db_icon_file(card_id):
    """The cached icon for an exact card id (``<id>.png`` or ``<id>.webp``, non-empty), or None."""
    if not DB_CARD_ID.match(str(card_id)):
        return None
    for ext in ("png", "webp"):
        path = DB_ICON_DIR / f"{card_id}.{ext}"
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _db_json(fetcher, url, target, refresh):
    if target.is_file() and not refresh:
        return json.loads(target.read_text(encoding="utf-8")), "present"
    data = fetcher.get(url)
    parsed = json.loads(data.decode("utf-8"))
    _write(target, data)
    return parsed, "downloaded"


def fetch_db(fetcher, refresh_list=False, max_bytes=DB_MAX_TOTAL_BYTES, limit=None):
    """ShinyColorsDB card enumeration + P/S card icons (resumable). Stops on 429/403 or the byte cap."""
    report = {"lists": {"downloaded": 0, "present": 0}, "cards": 0, "downloaded": 0, "present": 0, "bytes": 0,
              "failed": [], "skipped_ids": 0, "stopped": None, "catalog_complete": False}
    start = fetcher.total
    cards, seen = [], set()
    try:
        idols, status = _db_json(fetcher, DB_IDOL_LIST_URL, DB_DIR / "idolList.json", refresh_list)
        report["lists"][status] += 1
        if (not isinstance(idols, list) or not idols or len(idols) > DB_MAX_IDOLS
                or any(not isinstance(i, dict) or type(i.get("idolId")) is not int
                       or not 0 < i["idolId"] < 10000 for i in idols)):
            raise ValueError("偶像目录不完整或格式无效")
        idol_ids = list(dict.fromkeys(i["idolId"] for i in idols))
        for idol_id in idol_ids:
            info, status = _db_json(fetcher, db_idol_url(idol_id), DB_IDOLS_DIR / f"idolInfo-{idol_id}.json", refresh_list)
            report["lists"][status] += 1
            if not isinstance(info, dict) or not isinstance(info.get("cardLists"), list):
                raise ValueError(f"偶像 {idol_id} 的卡目录格式无效")
            for raw in info["cardLists"]:
                card = parse_db_card(raw, idol_id)
                if card is None:
                    report["skipped_ids"] += 1
                    continue
                if card["card_id"] not in seen:
                    seen.add(card["card_id"])
                    cards.append(card)
        report["catalog_complete"] = True
    except (RateLimited, OverflowError) as error:
        report["stopped"] = str(error)
    except (urllib.error.URLError, OSError, ValueError, PermissionError) as error:
        report["stopped"] = f"卡列表获取失败: {str(error)[:200]}"
    cards.sort(key=lambda c: c["card_id"])
    report["cards"] = len(cards)
    previous = {}
    if DB_CARDS_FILE.is_file():
        try:
            previous = {c["card_id"]: c for c in json.loads(DB_CARDS_FILE.read_text(encoding="utf-8")).get("cards", [])}
        except (OSError, ValueError, KeyError, TypeError):
            previous = {}
    jobs = []
    for card in cards:
        target = db_icon_file(card["card_id"])
        if target is not None:
            card["file"] = target.relative_to(SRC_DIR).as_posix()
            card["format"] = target.suffix[1:]
            old = previous.get(card["card_id"], {})
            data = target.read_bytes()
            card["sha256"], card["bytes"] = sha256_bytes(data), len(data)
            card["retrieved_at"] = old.get("retrieved_at", "")
            report["present"] += 1
        elif not report["stopped"] and (limit is None or len(jobs) < limit):
            jobs.append(card)
    stop = threading.Event()
    lock = threading.Lock()

    def download(card):
        if stop.is_set():
            return
        if fetcher.total - start >= max_bytes:
            with lock:
                report["stopped"] = report["stopped"] or f"超过 ShinyColorsDB 下载上限 {max_bytes // (1024 * 1024)} MB"
            stop.set()
            return
        try:
            data = fetcher.get(card["icon_url"])
        except (RateLimited, OverflowError) as error:
            with lock:
                report["stopped"] = report["stopped"] or str(error)
            stop.set()
            return
        except (urllib.error.URLError, OSError, ValueError, PermissionError) as error:
            with lock:
                report["failed"].append({"card_id": card["card_id"], "error": str(error)[:200]})
            return
        fmt = image_format(data)
        if fmt is None:
            with lock:
                report["failed"].append({"card_id": card["card_id"], "error": "不是 PNG/WebP"})
            return
        target = DB_ICON_DIR / f"{card['card_id']}.{fmt}"
        _write(target, data)
        card["file"], card["format"] = target.relative_to(SRC_DIR).as_posix(), fmt
        card["sha256"], card["bytes"] = sha256_bytes(data), len(data)
        card["retrieved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with lock:
            report["downloaded"] += 1

    if jobs and hasattr(fetcher, "robots_ok"):
        try:
            fetcher.robots_ok(jobs[0]["icon_url"])   # read robots.txt once, before the workers share the cache
        except (urllib.error.URLError, OSError, ValueError, RateLimited) as error:
            report["stopped"] = f"robots.txt 读取失败: {str(error)[:200]}"
            jobs = []
    if jobs:
        with ThreadPoolExecutor(max_workers=ICON_WORKERS) as pool:
            list(pool.map(download, jobs))
    report["bytes"] = fetcher.total - start
    if cards:
        _write(DB_CARDS_FILE, (json.dumps({
            "source": "https://shinycolors.moe/ (ShinyColorsDB API idolList/idolInfo + cf-static card icons)",
            "kind": "shinycolorsdb", "license": "none (extracted from the game); local private cache only, never commit or redistribute",
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cards": cards}, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return report


def check():
    enza = []
    for source in ENZA_SOURCES:
        target = SRC_DIR / source["path"]
        ok = target.is_file() and sha256_bytes(target.read_bytes()) == source["sha256"]
        enza.append({"id": source["id"], "path": target.relative_to(ROOT).as_posix(), "verified": ok, "purpose": source["purpose"]})
    icons = list(WIKI_ICON_DIR.glob("CardIcon-*.png")) if WIKI_ICON_DIR.is_dir() else []
    p_icons = list(WIKI_P_ICON_DIR.glob("CardIcon-*.png")) if WIKI_P_ICON_DIR.is_dir() else []
    db_icons = [p for p in DB_ICON_DIR.glob("*") if p.suffix in (".png", ".webp")] if DB_ICON_DIR.is_dir() else []
    tiles = [{"key": t["key"], "item": t["item"],
              "verified": tile_file(t["key"]).is_file() and sha256_bytes(tile_file(t["key"]).read_bytes()) == t["sha256"]}
             for t in ENZA_TILES]
    return {"enza": enza, "tiles": tiles, "db_cards": DB_CARDS_FILE.is_file(), "db_icons": len(db_icons),
            "db_icon_bytes": sum(p.stat().st_size for p in db_icons),
            "wiki_list": WIKI_LIST_FILE.is_file(), "wiki_icons": len(icons),
            "wiki_icon_bytes": sum(p.stat().st_size for p in icons),
            "wiki_p_list": WIKI_P_LIST_FILE.is_file(), "wiki_p_icons": len(p_icons),
            "wiki_p_icon_bytes": sum(p.stat().st_size for p in p_icons)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report local sources; no network")
    parser.add_argument("--fetch", action="store_true", help="download the pinned enza sources (and --wiki)")
    parser.add_argument("--wiki", action="store_true", help="with --fetch: wiki card lists + card thumbnails")
    parser.add_argument("--lists", nargs="+", choices=sorted(WIKI_LISTS), default=sorted(WIKI_LISTS),
                        help="with --wiki: which wiki lists (default: produce support)")
    parser.add_argument("--db", action="store_true", help="with --fetch: ShinyColorsDB card list + P/S card icons")
    parser.add_argument("--limit", type=int, default=None, help="with --db: download at most N new icons this run")
    parser.add_argument("--skip-enza", action="store_true", help="with --fetch: do not check/download the enza sources")
    parser.add_argument("--refresh-list", action="store_true", help="with --wiki: re-download the list page")
    parser.add_argument("--force", action="store_true", help="re-download enza sources and tiles even when verified")
    args = parser.parse_args(argv)
    out = {}
    if args.fetch:
        fetcher = Fetcher()
        if not args.skip_enza:
            out["enza"] = fetch_enza(fetcher, force=args.force)
            out["tiles"] = fetch_tiles(fetcher, force=args.force)
        if args.wiki:
            out["wiki"] = [fetch_wiki(fetcher, refresh_list=args.refresh_list, kind=kind) for kind in args.lists]
        if args.db:
            out["db"] = fetch_db(fetcher, refresh_list=args.refresh_list, limit=args.limit)
        out["requests"] = fetcher.requests
        out["bytes"] = fetcher.total
    else:
        out = check()
    sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
