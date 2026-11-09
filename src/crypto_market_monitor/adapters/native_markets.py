"""Parse official exchange catalogs into the local adapter's metadata schema."""

from crypto_market_monitor.adapters.native_http import PublicTransport
from crypto_market_monitor.models import DataError


def market(
    native_id: str,
    base: str,
    quote: str,
    active: bool,
    *,
    settle: str | None = None,
    size: str | None = None,
    ws_symbol: str | None = None,
) -> dict:
    return {
        "id": native_id,
        "symbol": f"{base}/{quote}" + (f":{settle}" if settle else ""),
        "base": base,
        "quote": quote,
        "active": active,
        "spot": settle is None,
        "swap": settle is not None,
        "linear": settle is not None,
        "settle": settle,
        "contractSize": size,
        "ws_symbol": ws_symbol,
    }


def okx_data(payload: dict) -> list[dict]:
    if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
        raise DataError("OKX public API error")
    return payload["data"]


def bybit_result(payload: dict) -> dict:
    if payload.get("retCode") != 0 or not isinstance(payload.get("result"), dict):
        raise DataError("Bybit public API error")
    return payload["result"]


def kraken_result(payload: dict) -> dict:
    if payload.get("error") != [] or not isinstance(payload.get("result"), dict):
        raise DataError("Kraken public API error")
    return payload["result"]


async def fetch_markets(
    transport: PublicTransport, venue: str, types: list[str], params: dict
) -> list[dict]:
    get = transport.request
    rows = []
    if venue == "okx":
        for kind in types:
            query = {"instType": "SPOT" if kind == "spot" else "SWAP", **params}
            for r in okx_data(await get(venue, "/api/v5/public/instruments", query)):
                if r["instType"] != query["instType"]:
                    raise DataError("OKX catalog type mismatch")
                swap = r["instType"] == "SWAP"
                if swap and r.get("ctType") != "linear":
                    continue
                base, quote = r["instId"].split("-")[:2]
                if swap and (r["settleCcy"] != quote or r.get("ctValCcy") != base):
                    raise DataError("Unsupported OKX contract quantity or settlement unit")
                rows.append(
                    market(
                        r["instId"],
                        r.get("baseCcy") or base,
                        r.get("quoteCcy") or quote,
                        r["state"] == "live",
                        settle=r["settleCcy"] if swap else None,
                        size=r["ctVal"] if swap else None,
                    )
                )
    elif venue == "binance":
        for kind in types:
            swap = kind != "spot"
            endpoint = "binance_perp" if swap else "binance_spot"
            path = "/fapi/v1/exchangeInfo" if swap else "/api/v3/exchangeInfo"
            for r in (await get(endpoint, path, params))["symbols"]:
                if swap and (
                    r.get("contractType") != "PERPETUAL" or r.get("marginAsset") != r["quoteAsset"]
                ):
                    continue
                rows.append(
                    market(
                        r["symbol"],
                        r["baseAsset"],
                        r["quoteAsset"],
                        r["status"] == "TRADING",
                        settle=r["marginAsset"] if swap else None,
                        size="1" if swap else None,
                    )
                )
    elif venue == "bybit":
        data = bybit_result(
            await get(venue, "/v5/market/instruments-info", {"category": "spot", **params})
        )
        if data.get("nextPageCursor"):
            raise DataError("Paginated Bybit catalog requires explicit symbol filter")
        rows = [
            market(r["symbol"], r["baseCoin"], r["quoteCoin"], r["status"] == "Trading")
            for r in data["list"]
        ]
    elif venue == "bitstamp":
        for r in await get(venue, "/api/v2/trading-pairs-info/"):
            base, quote = r["name"].split("/")
            # The reviewed adapter serves these two fiat pairs only.
            if r["url_symbol"] in {"usdtusd", "usdcusd"}:
                rows.append(
                    market(r["url_symbol"], base.upper(), quote.upper(), r["trading"] == "Enabled")
                )
    elif venue == "kraken":
        data = kraken_result(await get(venue, "/0/public/AssetPairs", params))
        for native_id, r in data.items():
            if not r.get("wsname") or "." in native_id:
                continue
            base, quote = r["wsname"].split("/")
            base, quote = ({"XBT": "BTC", "XDG": "DOGE"}.get(c, c) for c in (base, quote))
            rows.append(
                market(
                    native_id, base, quote, r.get("status") == "online", ws_symbol=f"{base}/{quote}"
                )
            )
    elif venue == "hyperliquid":
        if "swap" in types:
            data = await get(venue, "/info", {"type": "meta"})
            rows.extend(
                market(
                    r["name"],
                    r["name"],
                    "USDC",
                    not r.get("isDelisted", False),
                    settle="USDC",
                    size="1",
                )
                for r in data["universe"]
            )
        if "spot" in types:
            data = await get(venue, "/info", {"type": "spotMeta"})
            tokens = {t["index"]: t["name"] for t in data["tokens"]}
            for r in data["universe"]:
                base, quote = (tokens[i] for i in r["tokens"])
                rows.append(market(r["name"], base, quote, True))
    else:
        raise ValueError("Unsupported native venue")
    return rows
