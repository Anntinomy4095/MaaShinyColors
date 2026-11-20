// Original office vignettes. The existing character sprites stay local to this device.
export const OFFICE_CHARACTER_KEYS = ["mano", "hiori", "meguru", "kogane", "mamimi", "sakuya", "yuika", "kiriko",
  "kaho", "chiyoko", "juri", "rinze", "natsuha", "amana", "tenka", "chiyuki", "asahi", "fuyuko", "mei",
  "toru", "madoka", "koito", "hinana", "nichika", "mikoto", "luca", "hana", "haruki"];
// Blocking and native clip selection only. Props and body poses come from the original models.
const a = (key, x, animation = "wait", height = .70) => ({ key, x, y: .95, animation, height, layer: 2 });
const b = (...actors) => ({ actors, props: [], caption: "" });
export const OFFICE_STORIES = [
  { id: "illumination", label: "illumination STARS", short: "イルミネ", title: "窗边的鸽子", members: ["mano", "hiori", "meguru"], beats: [
    b(a("mano", .46, "wait", .79)),
    b(a("mano", .35), a("hiori", .74)),
    b(a("meguru", .17, "talk_ok"), a("mano", .47), a("hiori", .78)),
    b(a("meguru", .17), a("mano", .47), a("hiori", .78, "talk_wait")),
  ]},
  { id: "lantica", label: "L’Antica", short: "アンティーカ", title: "休息时间", members: ["kogane", "mamimi", "sakuya", "yuika", "kiriko"], beats: [
    b(a("kogane", .3), a("mamimi", .7)),
    b(a("kogane", .2, "talk_ok"), a("mamimi", .7), a("sakuya", .47)),
    b(a("kogane", .1), a("mamimi", .3), a("sakuya", .5), a("yuika", .7, "talk_ok"), a("kiriko", .9, "talk_wait")),
    b(a("kogane", .1, "talk_wait"), a("mamimi", .3), a("sakuya", .5, "talk_ok"), a("yuika", .7), a("kiriko", .9)),
  ]},
  { id: "hokago", label: "放課後クライマックスガールズ", short: "放クラ", title: "放课后", members: ["kaho", "chiyoko", "juri", "rinze", "natsuha"], beats: [
    b(a("kaho", .3), a("natsuha", .72)),
    b(a("kaho", .23, "talk_ok"), a("juri", .5), a("natsuha", .78, "talk_wait")),
    b(a("kaho", .1), a("chiyoko", .3, "talk_ok"), a("juri", .5), a("rinze", .7, "talk_wait"), a("natsuha", .9)),
    b(a("kaho", .1, "talk_ok"), a("chiyoko", .3), a("juri", .5, "talk_wait"), a("rinze", .7), a("natsuha", .9)),
  ]},
  { id: "alstroemeria", label: "ALSTROEMERIA", short: "アルスト", title: "一同休息", members: ["amana", "tenka", "chiyuki"], beats: [
    b(a("tenka", .58, "wait", .77)),
    b(a("chiyuki", .18, "talk_wait"), a("amana", .48, "talk_ok"), a("tenka", .78)),
    b(a("chiyuki", .18, "talk_ok"), a("amana", .48), a("tenka", .78, "talk_wait")),
    b(a("chiyuki", .18), a("amana", .48, "talk_wait"), a("tenka", .78)),
  ]},
  { id: "straylight", label: "Straylight", short: "ストレイ", title: "各自的小习惯", members: ["asahi", "fuyuko", "mei"], beats: [
    b(a("asahi", .42, "wait", .77)),
    b(a("asahi", .28), a("fuyuko", .72, "talk_wait")),
    b(a("asahi", .18, "talk_ok"), a("fuyuko", .78), a("mei", .48, "talk_ok")),
    b(a("asahi", .18), a("fuyuko", .78, "talk_wait"), a("mei", .48)),
  ]},
  { id: "noctchill", label: "noctchill", short: "ノクチル", title: "午后", members: ["toru", "madoka", "koito", "hinana"], beats: [
    b(a("toru", .35), a("madoka", .7)),
    b(a("toru", .2), a("madoka", .5), a("hinana", .8, "talk_ok")),
    b(a("toru", .12), a("madoka", .36), a("koito", .61, "talk_wait"), a("hinana", .86)),
    b(a("toru", .12, "talk_wait"), a("madoka", .36), a("koito", .61, "talk_ok"), a("hinana", .86)),
  ]},
  { id: "shhis", label: "SHHis", short: "シーズ", title: "练习间隙", members: ["nichika", "mikoto"], beats: [
    b(a("mikoto", .64, "wait", .76)),
    b(a("mikoto", .67), a("nichika", .3, "talk_wait")),
    b(a("mikoto", .67, "talk_ok"), a("nichika", .3)),
    b(a("mikoto", .67), a("nichika", .3, "talk_ok")),
  ]},
  { id: "cometik", label: "CoMETIK", short: "コメティック", title: "窗边", members: ["luca", "hana", "haruki"], beats: [
    b(a("haruki", .4, "wait", .77)),
    b(a("haruki", .32), a("hana", .65, "talk_wait")),
    b(a("haruki", .2), a("hana", .49, "talk_ok"), a("luca", .83)),
    b(a("haruki", .2, "talk_ok"), a("hana", .49), a("luca", .83)),
  ]},
];
