"""ページ本文の取り出し。"""
from collect import html_to_text


def test_img_alt_becomes_text():
    html = "<html><body><main><p>使える決済</p><img alt='AEON Pay' src='x.png'><img src='y.png'><script>var a=1</script></main></body></html>"
    text = html_to_text(html)
    assert "AEON Pay" in text and "var a" not in text
