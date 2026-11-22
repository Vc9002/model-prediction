from lib import *

d = load()
lg = lambda p: np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))


def logit_fit(X, y, it=50):
    b = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-X @ b))
        W = p * (1 - p)
        H = X.T @ (X * W[:, None])
        g = X.T @ (y - p)
        b = b + np.linalg.solve(H, g)
    p = 1 / (1 + np.exp(-X @ b))
    W = p * (1 - p)
    cov = np.linalg.inv(X.T @ (X * W[:, None]))
    return b, np.sqrt(np.diag(cov))


rows = []
for label, g in (
    [("ML all", d[d.market_type == "moneyline"])]
    + [("ML regime " + r, d[(d.market_type == "moneyline") & (d.regime == r)]) for r in "ABC"]
    + [("ML B+C", d[(d.market_type == "moneyline") & (d.regime != "A")])]
):
    X = np.column_stack([np.ones(len(g)), lg(g.qp.values), lg(g.sp.values) - lg(g.qp.values)])
    b, se = logit_fit(X, g.y.values.astype(float))
    rows.append(
        {
            "set": label,
            "n": len(g),
            "b logit(mkt)": round(b[1], 3),
            "b [logit(served)-logit(mkt)]": round(b[2], 3),
            "se": round(se[2], 3),
            "z": round(b[2] / se[2], 2),
            "noise": "n<30" if len(g) < 30 else "",
        }
    )
s = (
    "### Moneyline: logistic y ~ logit(mkt) + [logit(served) - logit(mkt)]. b>0 on the second term means the model adds information beyond the price; b=1 on the first means the price is calibrated.\n"
    + md(pd.DataFrame(rows))
)
open("t5b.md", "w").write(s)
print(s)
