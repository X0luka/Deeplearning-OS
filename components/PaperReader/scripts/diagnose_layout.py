from collections import Counter

import pdfplumber

for alias in ("attention", "bert", "resnet"):
    with pdfplumber.open(f"var/evaluation/{alias}.pdf") as pdf:
        for num, p in enumerate(pdf.pages[:8], 1):
            words = p.extract_words(x_tolerance=2, y_tolerance=3)
            counts = Counter(
                round(w["x0"] / 3) * 3
                for w in words
                if p.width / 2 < w["x0"] < p.width / 2 + 40
                and len(w["text"]) >= 3
                and w["text"][:3].isalpha()
            )
            print(alias, num, round(p.width, 1), counts.most_common(3))
