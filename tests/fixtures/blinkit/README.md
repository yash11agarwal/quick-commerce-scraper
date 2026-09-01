# Blinkit fixtures

**Provenance: synthesised, not captured.** Live capture from the build environment was not
possible on 1 September 2026 (Blinkit's Cloudflare front answered HTTP 403 to non-browser
clients from that egress, and the environment's egress gateway would not complete a TLS
handshake from Chromium). These files were therefore built by hand from the two things the
playbooks document: the Redux slice shape (playbook 01 section 3.2, playbook 02 section 2.3)
and the 18 August 2026 data table for 700048 / "Mango" (playbook 01 section 7.1).

What that means:

- Every value in `normal.json`, `out_of_stock.json` and `missing_mrp.json` is a value the
  playbook table records (rows 1, 2, 3, 5 and 12). Keys whose value the playbook never
  sampled (`brand_name`, `merchant_type`, `image`, `click_action`, `rating`, `eta_tag`,
  `offer_tag`) are either omitted or `null`. Nothing was invented.
- The nesting (`name.text`, `variant.text`, `normal_price.text`, `mrp.text`, a `data`,
  `tracking`, `widget_type`, `layout_config` quartet per snippet) is the documented one.
  The wrapper `{"searchProductBffData": ...}` is how the adapter stores the slice.
- `no_product_rows.json` is **not** a captured empty result. The empty-result signature is
  OPEN (spec section 9). The parser must treat it as `SCHEMA_DRIFT` with reason
  `empty_signature_unconfirmed`, and the test asserts exactly that.
- `location_evidence_shape.json` is a placeholder for the `data.location` / `data.merchant`
  / `data.eta` / `data.addressesV2` dump. The real slices were never captured.

**Replace these with real captures at the first successful live run:**

```
python -m qcom smoke --platform blinkit --pincode 700048 --term "Mango" --city Kolkata --save-captures captures/blinkit
```

then trim the `redux_store` capture to about five product snippets (keep one at inventory 0
and one with `mrp: null`), copy it over `normal.json`, and update this README with the capture
date. Until then, a green parser test proves the parser matches the playbook, not the site.
