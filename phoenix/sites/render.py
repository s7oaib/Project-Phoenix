# ruff: noqa: E501
"""SiteSpec -> one self-contained responsive HTML file (inline CSS, no JS libs).

Pure string rendering. Every model- or user-supplied string goes through
``html.escape``; the only unescaped values are fixed template text, validated
hex colors and base64 data URIs built from allow-listed image types.
Site types change tone and shape (radius, density, CTA); layouts a/b/c change
the composition of each section kind; themes are the colors plus font.
"""

import base64
from collections.abc import Callable
from html import escape

from phoenix.sites.spec import Item, Section, SiteSpec, _lum

ImageLoader = Callable[[str], str | None]  # asset name -> data URI or None

FONTS = {
    "serif": "'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif",
    "sans": "'Inter','Segoe UI Variable Text','Segoe UI',system-ui,-apple-system,sans-serif",
    "mono": "ui-monospace,'Cascadia Code',Consolas,'SF Mono',Menlo,monospace",
    "display": "'Segoe UI Variable Display','Avenir Next','Trebuchet MS',system-ui,sans-serif",
}
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
CTA = {"landing": "Get started", "portfolio": "See the work", "business": "Contact us"}
RADIUS = {"landing": "22px", "portfolio": "4px", "business": "10px"}

CSS = """
*{box-sizing:border-box;margin:0}html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--tx);font-family:var(--font);line-height:1.6;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.mut{color:var(--mut)}a{color:inherit}
.sec{padding:clamp(56px,9vw,120px) clamp(20px,6vw,88px);border-top:1px solid var(--line);
animation:rise .7s ease both}
.sec>*{max-width:1120px;margin-inline:auto}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.sec{animation:none}html{scroll-behavior:auto}}
h1,h2,h3{line-height:1.08;letter-spacing:-.025em;text-wrap:balance}
.f-display h1,.f-display h2{font-weight:800;letter-spacing:-.045em}
.f-mono h1,.f-mono h2{letter-spacing:-.04em;font-weight:600}
.f-serif h1,.f-serif h2{font-weight:500;letter-spacing:-.02em}
h1{font-size:clamp(2.4rem,6.5vw,5rem)}h2{font-size:clamp(1.7rem,3.6vw,2.8rem)}
h3{font-size:1.15rem;letter-spacing:-.01em}
p{text-wrap:pretty}.lead{font-size:clamp(1.05rem,1.6vw,1.3rem);color:var(--mut);max-width:60ch;margin-top:1.1rem}
.eyebrow{display:inline-block;font-size:.75rem;letter-spacing:.14em;text-transform:uppercase;
color:var(--p);border:1px solid color-mix(in srgb,var(--p) 40%,var(--bg));padding:.35rem .8rem;
border-radius:999px;margin-bottom:1.4rem}
.btn{display:inline-block;margin-top:2rem;padding:.9rem 1.6rem;background:var(--p);color:var(--onp);
text-decoration:none;font-weight:600;border-radius:calc(var(--r)/2);transition:transform .2s,box-shadow .2s;
box-shadow:0 10px 30px -12px var(--p)}
.btn:hover{transform:translateY(-2px);box-shadow:0 16px 36px -12px var(--p)}
.card{background:var(--surf);border:1px solid var(--line);border-radius:var(--r);padding:1.7rem;
transition:border-color .2s,transform .2s}
.card:hover{border-color:color-mix(in srgb,var(--p) 55%,var(--line));transform:translateY(-3px)}
.card p{color:var(--mut);margin-top:.6rem}
.grid{display:grid;gap:1.1rem;margin-top:3rem;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.t-business .sec{padding-block:clamp(48px,7vw,92px)}.t-portfolio .sec{padding-block:clamp(72px,11vw,150px)}
.k-hero{border-top:0;position:relative;overflow:hidden}
.k-hero.l-a{text-align:center;padding-top:clamp(90px,14vw,190px)}
.k-hero.l-a .lead{margin-inline:auto}
.k-hero.l-a::before{content:"";position:absolute;inset:-30% 10% auto;height:70%;pointer-events:none;
background:radial-gradient(closest-side,color-mix(in srgb,var(--p) 30%,transparent),transparent);filter:blur(30px)}
.k-hero>*{position:relative}
.split{display:grid;gap:3rem;align-items:center;grid-template-columns:1.1fr .9fr}
.vis{aspect-ratio:4/3;border-radius:var(--r);border:1px solid var(--line);overflow:hidden;
background:linear-gradient(135deg,color-mix(in srgb,var(--p) 55%,var(--bg)),var(--surf) 70%);position:relative}
.vis::after{content:"";position:absolute;width:55%;aspect-ratio:1;right:-8%;bottom:-14%;border-radius:50%;
background:color-mix(in srgb,var(--p) 70%,var(--bg));opacity:.5}
.vis img,.tile img,.fig img{width:100%;height:100%;object-fit:cover;display:block}
.k-hero.l-c h1{font-size:clamp(3rem,10vw,8rem);max-width:14ch}
.k-hero.l-c .lead{margin-left:0}
.fig{margin-top:2.4rem;border-radius:var(--r);overflow:hidden;border:1px solid var(--line);max-height:420px}
.rows>div{display:grid;grid-template-columns:3.5rem 1fr 1.4fr;gap:1.4rem;padding:1.5rem 0;border-top:1px solid var(--line);align-items:baseline}
.rows{margin-top:2.4rem}.rows .n{color:var(--p);font-variant-numeric:tabular-nums}.rows p{color:var(--mut)}
.l-c .bento{display:grid;gap:1.1rem;margin-top:3rem;grid-template-columns:repeat(3,1fr)}
.bento .card:first-child{grid-column:span 2;grid-row:span 2;display:flex;flex-direction:column;justify-content:flex-end;min-height:260px}
.tiles{display:grid;gap:.9rem;margin-top:3rem;grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
.tile{aspect-ratio:1;border-radius:var(--r);overflow:hidden;position:relative;border:1px solid var(--line);
background:linear-gradient(145deg,color-mix(in srgb,var(--p) var(--m),var(--bg)),var(--surf))}
.tile span{position:absolute;left:1rem;bottom:.9rem;font-size:.85rem;font-weight:600;
text-shadow:0 1px 8px var(--bg)}
.l-b .tiles{grid-template-columns:repeat(4,1fr)}.l-b .tile:first-child{grid-column:span 2;grid-row:span 2}
.l-c .tiles{display:flex;overflow-x:auto;scroll-snap-type:x mandatory}
.l-c .tile{flex:0 0 min(300px,72vw);scroll-snap-align:start}
.quote{font-size:1.15rem}.quote cite{display:block;margin-top:1.1rem;font-style:normal;font-size:.9rem;color:var(--mut)}
.quote::before{content:"\\201C";display:block;font-size:3.4rem;line-height:.6;color:var(--p)}
.l-b .big{font-size:clamp(1.5rem,3vw,2.3rem);margin-top:3rem;letter-spacing:-.02em;line-height:1.25}
.l-c .stack{max-width:760px;margin-top:3rem}.stack .quote{padding:2rem 0;border-top:1px solid var(--line)}
.tier h3{font-size:1.3rem}.tier.hot{border-color:var(--p);background:color-mix(in srgb,var(--p) 9%,var(--surf))}
.l-b .rows.tiers>div{grid-template-columns:1fr 2fr}
.band{background:var(--p);color:var(--onp);border-radius:var(--r);padding:clamp(32px,6vw,72px);text-align:center}
.band .lead{color:inherit;opacity:.85;margin-inline:auto}.band .btn{background:var(--onp);color:var(--p);box-shadow:none}
.field{display:block;margin-top:1rem;padding:.9rem 1rem;border:1px solid var(--line);border-radius:calc(var(--r)/2);
color:var(--mut);background:var(--surf)}
.info{margin-top:2rem;display:grid;gap:.6rem}.info b{color:var(--p);font-weight:600;margin-right:.6rem}
.k-contact.l-a{text-align:center}.k-contact.l-a .lead{margin-inline:auto}
footer.sec{padding-block:48px;color:var(--mut);font-size:.95rem}
.fcols{display:grid;gap:2rem;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-top:1.6rem}
.fcols b{color:var(--tx)}.word{font-size:clamp(3rem,14vw,10rem);font-weight:800;letter-spacing:-.06em;
line-height:.9;color:color-mix(in srgb,var(--tx) 10%,var(--bg))}
@media (max-width:820px){.split{grid-template-columns:1fr}.rows>div,.l-b .rows.tiers>div{grid-template-columns:1fr;gap:.4rem}
.l-c .bento{grid-template-columns:1fr}.bento .card:first-child{grid-column:auto;grid-row:auto}.l-b .tiles{grid-template-columns:1fr 1fr}}
"""
SELECT_CSS = (
    ".phx-sel{outline:2px solid #22d3ee;outline-offset:-2px}"
    "[data-section-id]:hover{outline:1px dashed #22d3ee88;outline-offset:-2px;cursor:pointer}"
)
SELECT_JS = (
    "document.addEventListener('click',function(e){var s=e.target.closest('[data-section-id]');"
    "if(!s)return;e.preventDefault();document.querySelectorAll('.phx-sel').forEach(function(x)"
    "{x.classList.remove('phx-sel')});s.classList.add('phx-sel');window.parent.postMessage("
    "{type:'phoenix-select',sectionId:s.getAttribute('data-section-id')},'*')},true);"
)


def _e(value: str) -> str:
    return escape(value, quote=True)


def data_uri(name: str, raw: bytes) -> str | None:
    mime = MIME.get("." + name.rsplit(".", 1)[-1].lower())
    return (
        None if mime is None else f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    )


def _head(s: Section, tag: str = "h2", lead: bool = True) -> str:
    body = f'<p class="lead">{_e(s.body)}</p>' if lead and s.body else ""
    return f"<{tag}>{_e(s.title)}</{tag}>{body}"


def _card(i: Item, cls: str = "card") -> str:
    return f'<div class="{cls}"><h3>{_e(i.title)}</h3><p>{_e(i.body)}</p></div>'


def _img(src: str | None, cls: str) -> str:
    return f'<div class="{cls}"><img src="{src}" alt=""></div>' if src else ""


def _hero(s: Section, spec: SiteSpec, img: str | None, first_next: str) -> str:
    eyebrow = f'<span class="eyebrow">{_e(spec.name)}</span>'
    cta = f'<a class="btn" href="#{first_next}">{CTA[spec.type]}</a>'
    text = (
        f"{eyebrow}<h1>{_e(s.title)}</h1>"
        + (f'<p class="lead">{_e(s.body)}</p>' if s.body else "")
        + cta
    )
    if s.layout == "b":
        pic = f'<img src="{img}" alt="">' if img else ""
        vis = f'<div class="vis">{pic}</div>'
        return f'<div class="split"><div>{text}</div>{vis}</div>'
    return text + (_img(img, "fig") if s.layout == "c" else "")


def _features(s: Section, img: str | None) -> str:
    cards = s.items
    if s.layout == "b":
        rows = "".join(
            f'<div><span class="n">{n:02d}</span><h3>{_e(i.title)}</h3><p>{_e(i.body)}</p></div>'
            for n, i in enumerate(cards, 1)
        )
        return f'{_head(s)}<div class="rows">{rows}</div>'
    if s.layout == "c":
        return f'{_head(s)}<div class="bento">{"".join(_card(i) for i in cards)}</div>'
    return f'{_head(s)}<div class="grid">{"".join(_card(i) for i in cards)}</div>'


def _gallery(s: Section, img: str | None) -> str:
    items = s.items or tuple(Item(title="", body="") for _ in range(6))
    tiles = []
    for n, i in enumerate(items):
        pic = f'<img src="{img}" alt="">' if img and n == 0 else ""
        cap = f"<span>{_e(i.title)}</span>" if i.title else ""
        tiles.append(
            f'<div class="tile" style="--m:{28 + (n * 13) % 50}%">{pic}{cap}</div>'
        )
    return f'{_head(s)}<div class="tiles">{"".join(tiles)}</div>'


def _testimonials(s: Section, img: str | None) -> str:
    def q(i: Item) -> str:
        return f'<blockquote class="quote card"><p>{_e(i.body)}</p><cite>{_e(i.title)}</cite></blockquote>'

    if s.layout == "b" and s.items:
        first, rest = s.items[0], s.items[1:]
        return (
            f'{_head(s, lead=False)}<p class="big">{_e(first.body)}</p>'
            f'<p class="mut">{_e(first.title)}</p>'
            f'<div class="grid">{"".join(q(i) for i in rest)}</div>'
        )
    if s.layout == "c":
        stack = "".join(
            f'<blockquote class="quote"><p>{_e(i.body)}</p><cite>{_e(i.title)}</cite></blockquote>'
            for i in s.items
        )
        return f'{_head(s)}<div class="stack">{stack}</div>'
    return f'{_head(s)}<div class="grid">{"".join(q(i) for i in s.items)}</div>'


def _pricing(s: Section, img: str | None) -> str:
    hot = len(s.items) // 2 if len(s.items) > 2 else 0
    if s.layout == "b":
        rows = "".join(
            f"<div><h3>{_e(i.title)}</h3><p>{_e(i.body)}</p></div>" for i in s.items
        )
        return f'{_head(s)}<div class="rows tiers">{rows}</div>'
    if s.layout == "c":
        hot = 0
    cards = "".join(
        _card(i, "card tier hot" if n == hot else "card tier")
        for n, i in enumerate(s.items)
    )
    return f'{_head(s)}<div class="grid">{cards}</div>'


def _contact(s: Section, img: str | None) -> str:
    info = "".join(f"<p><b>{_e(i.title)}</b>{_e(i.body)}</p>" for i in s.items)
    info = f'<div class="info">{info}</div>' if info else ""
    if s.layout == "b":
        form = "".join(
            f'<span class="field">{f}</span>' for f in ("Name", "Email", "Message")
        )
        return f'<div class="split"><div>{_head(s)}{info}</div><div>{form}</div></div>'
    btn = f'<a class="btn" href="#">{CTA["business"]}</a>'
    if s.layout == "c":
        return f'<div class="band">{_head(s)}{btn}</div>'
    return f"{_head(s)}{info}{btn}"


def _footer(s: Section, spec: SiteSpec) -> str:
    if s.layout == "b":
        cols = "".join(
            f"<div><b>{_e(i.title)}</b><p>{_e(i.body)}</p></div>" for i in s.items
        )
        return (
            f'<b>{_e(spec.name)}</b><p>{_e(s.body)}</p><div class="fcols">{cols}</div>'
        )
    if s.layout == "c":
        return f'<p class="word">{_e(spec.name)}</p><p>{_e(s.body)}</p>'
    return f"<p><b>{_e(spec.name)}</b> &middot; {_e(s.body)}</p>"


def render(
    spec: SiteSpec, *, load_image: ImageLoader | None = None, select: bool = False
) -> str:
    """Render the whole page. select=True adds click-to-select for the preview."""
    t = spec.theme
    onp = "#ffffff" if _lum(t.primary) < 0.4 else "#111111"
    first_next = spec.sections[1].id
    parts = []
    for s in spec.sections:
        img = load_image(s.image) if load_image and s.image else None
        if s.kind == "hero":
            inner = _hero(s, spec, img, first_next)
        elif s.kind == "footer":
            inner = _footer(s, spec)
        else:
            inner = {
                "features": _features,
                "gallery": _gallery,
                "testimonials": _testimonials,
                "pricing": _pricing,
                "contact": _contact,
            }[s.kind](s, img)
            if img and s.kind != "gallery":
                inner += _img(img, "fig")
        tag = "footer" if s.kind == "footer" else "section"
        parts.append(
            f'<{tag} class="sec k-{s.kind} l-{s.layout}" id="{s.id}" data-section-id="{s.id}">'
            f"{inner}</{tag}>"
        )
    vars_ = (
        f"--p:{t.primary};--bg:{t.bg};--tx:{t.text};--onp:{onp};--r:{RADIUS[spec.type]};"
        f"--font:{FONTS[t.font]};"
        "--mut:color-mix(in srgb,var(--tx) 64%,var(--bg));"
        "--line:color-mix(in srgb,var(--tx) 13%,var(--bg));"
        "--surf:color-mix(in srgb,var(--tx) 5%,var(--bg))"
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(spec.name)}</title><style>:root{{{vars_}}}{CSS}"
        f"{SELECT_CSS if select else ''}</style></head>"
        f'<body class="t-{spec.type} f-{t.font}">{"".join(parts)}'
        f"{f'<script>{SELECT_JS}</script>' if select else ''}</body></html>"
    )
