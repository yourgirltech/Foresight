-- ============================================================================
-- Foresight — Phase 4 / 03 follow-up: track when a card image has been purged.
-- ----------------------------------------------------------------------------
-- 03 stores the card image in the private card-scans bucket and keeps it only
-- briefly (ocr.CONFIRM_IMAGE_RETENTION_DAYS after a confirm; 0 after a reject).
-- `image_retain_until` says WHEN the object should go; this column records that
-- it HAS gone, so:
--   * the purge job (scripts/purge_expired_card_images.py) is idempotent — it
--     skips rows already purged;
--   * GET /api/card-scans/{id} knows not to mint a signed URL for a gone object;
--   * a query can tell "image retained" from "image deleted, text kept".
-- The extracted text on card_scans is unaffected — only the picture is removed.
-- ============================================================================
alter table public.card_scans
  add column image_purged_at timestamptz;

comment on column public.card_scans.image_purged_at is
  'Set by the retention purge job when the Storage object at image_path has been deleted. Null => the image is still stored. The extracted fields on this row are kept regardless.';
