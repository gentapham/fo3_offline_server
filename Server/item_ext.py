"""Mo rong vat pham dung chung cho cac tinh nang an (seasoncare, craft).

Khong sua http_config: boc (wrap) use_item_def va use_item trong module http_config.
- effect["staticdata"] (dict): truong staticdata them vao item (vd. season/mingrade cho
  ITEM_ENHANCEMENT) - gui kem trong /coach/me items, /shop/list, /static/cache/item.
- effect["nouse"] (str): /item/<id>/use bi chan voi ly do nay (vat pham chi dung o man khac).
"""
hc = None


def add_items(items):
    """Them ITEMS moi; bao loi neu trung id voi vat pham/goi da co."""
    for itemid in items:
        for table in (hc.ITEMS, hc.PACKS, hc.PACKAGES, hc.MONTHLY):
            if itemid in table and table[itemid] != items[itemid]:
                raise ValueError(f"item id {itemid} da duoc dung")
    hc.ITEMS.update(items)


def install(module):
    global hc
    hc = module
    if getattr(hc, "_item_ext_installed", False):
        return
    hc._item_ext_installed = True
    base_def, base_use = hc.use_item_def, hc.use_item

    def use_item_def(itemid):
        d = base_def(itemid)
        extra = hc.ITEMS[itemid][3].get("staticdata") if isinstance(hc.ITEMS[itemid][3], dict) else None
        if extra:
            d.update(extra)
        return d

    def use_item(st, slot, coach, it, req):
        eff = hc.ITEMS[it["itemid"]][3]
        if isinstance(eff, dict) and eff.get("nouse"):
            return {"result": False, "reason": eff["nouse"]}
        return base_use(st, slot, coach, it, req)

    hc.use_item_def = use_item_def
    hc.use_item = use_item
