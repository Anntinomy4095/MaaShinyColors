// Search folding for the card / item pickers (2026-10-26, user: 搜索要支持简体、偶像别名和空格分词).
// The query and the searched text go through the same `fold`, so a player may type 「樱木真乃」 / 「桜木」 / 「摩美美」 and still hit
// the game's own spelling (櫻木真乃, 田中摩美々): Simplified / shinjitai / traditional variants map to the game's characters, 「々」
// repeats the character before it, half- / full-width and case are unified and katakana is folded to hiragana.
// The tables are character maps and a few public unit / reading aliases only — no card list or private data lives here.

/** Groups of characters: the first is the game's spelling (idol names, card titles), the rest fold into it. */
export const FOLD_GROUPS = [
  // idol names (including the Oshi no Ko collab idols): Simplified / shinjitai / traditional -> the game's spelling
  "櫻樱桜", "風风", "鐘钟鍾", "瀬濑瀨", "織织", "岡冈", "結结", "華华", "霧雾", "穂穗", "園园", "樹树", "葉叶", "沢泽澤",
  "優优", "愛爱", "倉仓", "円圆圓", "雛雏", "緋绯", "鈴铃", "鳩鸠", "糸丝絲", "黒黑", "馬马", "灯燈", "凛凜", "浅淺",
  "恋戀", "真眞", "峰峯", "宮宫",
  // characters of card titles (Japanese form -> Simplified; 気 and 後 also take the traditional / Simplified spelling)
  "気气氣", "後后", "間间", "夢梦", "見见", "環环", "時时", "純纯", "窓窗", "戦战", "陽阳", "遠远", "場场", "熱热", "紅红",
  "満满", "緒绪", "鳴鸣", "伝传", "長长", "騎骑", "願愿", "紡纺", "遊游", "輪轮", "機机", "線线", "実实", "話话", "団团",
  "燦灿", "課课", "歩步", "語语", "辺边", "楽乐", "帰归", "選选", "跡迹", "輝辉", "憶忆", "無无", "様样", "覚觉", "聞闻",
  "務务", "訓训", "焼烧", "両两", "還还", "譚谭", "謹谨", "賀贺", "綺绮", "湯汤", "聖圣", "試试", "餅饼", "報报", "贈赠",
  "晩晚", "頃顷", "幾几", "過过", "達达", "姫姬", "違违", "魚鱼", "別别", "終终", "階阶", "雲云", "儘尽", "顔颜", "綿绵",
  "証证", "竜龙", "誘诱", "書书", "問问", "閑闲", "誰谁", "識识", "鱗鳞", "縷缕", "氷冰", "電电", "録录", "藍蓝", "兎兔",
  "囲围", "駆驱", "門门", "飾饰", "鍋锅", "粋粹", "義义", "賑赈", "畳叠", "鈿钿", "釵钗", "計计", "羅罗", "楓枫", "寛宽",
  "捨舍", "鉄铁", "絶绝", "続续", "撃击", "煙烟", "強强", "習习", "蘭兰", "偉伟", "貧贫", "縫缝", "離离", "導导", "飴饴",
  "開开", "談谈", "閉闭", "癒愈", "毎每", "勝胜", "獲获", "僕仆", "悪恶", "滅灭", "誕诞", "側侧", "銀银", "準准", "敵敌",
  "紋纹", "張张", "極极", "緑绿", "響响", "冊册", "専专", "雑杂", "軌轨", "減减", "頬颊", "論论", "馴驯", "飛飞", "決决",
  "襲袭", "捜搜", "隊队", "躍跃", "創创", "偵侦", "絵绘", "進进", "戯戏", "乗乘", "涼凉", "紺绀", "渦涡", "淵渊", "鏡镜",
  "寧宁", "詠咏", "態态", "備备", "騒骚", "質质", "為为", "標标", "現现", "転转", "個个", "節节", "巣巢", "輩辈", "塗涂",
  "連连", "測测", "帯带", "組组", "錯错", "酔醉", "昇升", "鳥鸟", "籠笼", "憐怜", "億亿", "僥侥",
  // a few common ones not on a card yet
  "経经", "関关", "対对",
];

const FOLD = new Map();
for (const group of FOLD_GROUPS) {
  const [to, ...from] = [...group];
  for (const ch of from) FOLD.set(ch, to);
}

/**
 * Normal form of a query or of the searched text: NFKC (full-width -> half-width, half-width kana -> kana), lower case, the
 * character maps above, 「々」 -> the previous character (摩美々 = 摩美美) and katakana -> hiragana (ルカ = るか).
 */
export function fold(text) {
  let out = "";
  let prev = "";
  for (const ch of String(text ?? "").normalize("NFKC").toLowerCase()) {
    let c = FOLD.get(ch) ?? ch;
    if (c >= "ァ" && c <= "ヶ") c = String.fromCharCode(c.charCodeAt(0) - 0x60);
    else if (c === "々" && prev && !/\s/.test(prev)) c = prev;
    out += c;
    prev = c;
  }
  return out;
}

/** The folded words of a query (split on any whitespace, incl. the full-width space); [] for an empty query. */
export function queryWords(query) {
  return fold(query).split(/\s+/).filter(Boolean);
}

// Rarity codes are matched as whole words: 「SR」 must not hit 「SSR」 (a substring of it), 「R」 not every name with an r.
const WHOLE_WORD = /^(?:ur|ssr|sr|r|n)$/;
const WORD_CHAR = /[a-z0-9]/;

function hasWord(text, word) {
  for (let at = text.indexOf(word); at >= 0; at = text.indexOf(word, at + 1)) {
    if (!WORD_CHAR.test(text[at - 1] || "") && !WORD_CHAR.test(text[at + word.length] || "")) return true;
  }
  return false;
}

/** True when the already folded `text` contains every word (AND); an empty word list matches everything. */
export function hasWords(text, words) {
  return words.every((word) => (WHOLE_WORD.test(word) ? hasWord(text, word) : text.includes(word)));
}

// ---------------------------------------------------------------- aliases (idol number = digits 4-6 of a card id)
// Units: Japanese name, English name, short names and the Simplified fan names. Idols: family / given name readings and the
// Chinese given names that differ from the Japanese kanji (八宫巡, 芹泽朝日, …; the rest fold from the kanji already).
const UNIT_WORDS = [
  ["イルミネーションスターズ illumination stars イルミネ", ["001", "002", "003"]],
  ["アンティーカ l'antica lantica antica 安蒂卡", ["004", "005", "006", "007", "008"]],
  ["放課後クライマックスガールズ hokago climax girls 放クラ 放课后气候少女", ["009", "010", "011", "012", "013"]],
  ["アルストロメリア alstroemeria アルスト 六出花", ["014", "015", "016"]],
  ["ストレイライト straylight stray light ストレイ", ["017", "018", "019"]],
  ["ノクチル noctchill", ["020", "021", "022", "023"]],
  ["シーズ shhis", ["024", "025"]],
  ["コメティック cometik", ["026", "027", "028"]],
  ["b小町 b-komachi bkomachi 推しの子 推之子 oshinoko", ["801", "802", "803", "804"]],
];

const IDOL_WORDS = {
  "001": "さくらぎ まの", "002": "かぜの ひおり", "003": "はちみや めぐる 八宫巡 巡", "004": "つきおか こがね",
  "005": "たなか まみみ", "006": "しらせ さくや", "007": "みつみね ゆいか", "008": "ゆうこく きりこ",
  "009": "こみや かほ", "010": "そのだ ちよこ", "011": "さいじょう じゅり", "012": "もりの りんぜ",
  "013": "ありすがわ なつは", "014": "おおさき あまな", "015": "おおさき てんか", "016": "くわやま ちゆき",
  "017": "せりざわ あさひ 芹泽朝日 朝日", "018": "まゆずみ ふゆこ", "019": "いずみ めい", "020": "あさくら とおる",
  "021": "ひぐち まどか", "022": "ふくまる こいと", "023": "いちかわ ひなな", "024": "ななくさ にちか 七草日花 日花",
  "025": "あけた みこと", "026": "いかるが るか 斑鸠露卡 露卡", "027": "すずき はな", "028": "いくた はるき 郁田晴希 晴希",
  "091": "ななくさ はづき", "801": "ほしの るびー ruby 露比", "802": "ありま かな 有马加奈 加奈",
  "803": "めむちょ mem-cho memcho mem酱 梅姆", "804": "くろかわ あかね 黑川茜 茜",
};

/** Alias words for the idol with this number ("" when unknown), for a card's `search` text. */
export function idolWords(number) {
  const units = UNIT_WORDS.filter(([, numbers]) => numbers.includes(number)).map(([words]) => words);
  return [IDOL_WORDS[number], ...units].filter(Boolean).join(" ");
}

const SPECIALTY_WORDS = {
  Vocal: "声乐 歌唱 ボーカル", Dance: "舞蹈 ダンス", Visual: "视觉 ビジュアル", Mental: "精神 メンタル", Balanced: "均衡 バランス",
};

/** Chinese / Japanese words for the attributes named in a wiki specialty ("Dance (Vocal)" -> 舞蹈 … 声乐 …). */
export function specialtyWords(specialty) {
  return Object.entries(SPECIALTY_WORDS).filter(([word]) => (specialty || "").includes(word)).map(([, words]) => words).join(" ");
}
