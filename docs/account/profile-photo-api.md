# Account profile photo API

Customers can upload, replace, re-crop, or remove their account photo. These
actions are independent of saving personal details, so changing a photo preserves
unfinished name or address edits.

## How a photo change works

1. Load the saved avatar, retained source image, and crop coordinates.
2. Let the customer choose an image and a square crop. The frontend shows a
   circular preview; the backend validates and applies the actual crop.
3. Save an optimized source for future re-cropping and a separate avatar for
   display. Personal details and recipients remain unchanged.
4. Return the saved photo data. The frontend updates its shared account photo so
   the header uses the confirmed URL without a separate photo request.

A **crop** selects part of the full image. The **source ID** identifies which
saved image is being adjusted. If it changed while the editor was open, the
backend rejects the old source and the frontend reloads before another edit.
Uncertain saves/removals also trigger a reload; if that fails, photo actions wait
for a successful read.

## Request and response contract

`GET`, `PUT`, and `DELETE /api/account/profile/photo/` require Bearer authentication
and act only on the current account. All successful operations return 200:

```json
{
  "profile_image": "https://storage.example/users/profile/avatar.webp",
  "original_image": "https://storage.example/users/profile/originals/source.webp",
  "source_id": "users/profile/originals/source.webp",
  "crop": { "x": 0.25, "y": 0, "width": 0.5, "height": 1 }
}
```

Empty photos return null URLs and crop, and an empty source_id. GET does not
write. The existing personal-details GET/PUT contract is unchanged; photo fields
remain read-only there. Original-image URLs use the configured media storage's
access behavior, just like other image fields; the API does not introduce private
media delivery.

## Upload and crop

PUT multipart/form-data accepts an `image` file and a `crop` JSON string containing
x, y, width and height. Coordinates are fractions of the full **oriented source**
width/height, with top-left origin; values must be finite, positive for dimensions,
inside the image, and represent a square in pixels (2px tolerance for rounding).

Accept static JPEG, PNG, or WebP by decoded content, regardless of filename or
client MIME type. Reject corrupt/animated/unsupported images, files over 5 MiB
(5 × 1024 × 1024 bytes), and images over 25 megapixels before full decoding.
Normalize EXIF orientation and strip EXIF/ICC metadata when encoding.

The retained full-composition source is resized to fit within 2048 × 2048 without
upscaling and encoded as WebP at quality 85. The selected crop generates a
512 × 512 WebP avatar at quality 85. The retained source is an optimized original,
not a byte-for-byte copy of the upload. Compression is not guaranteed to reduce
bytes for every small input. Transparency is preserved.

## Re-crop and removal

PUT application/json accepts `crop` as an object and `source_id` from the last
read. The endpoint reuses the retained source, never the previous cropped avatar.
An absent or changed source returns a 400 source_id error; reload before editing.
Old seeded photos with no retained source use their existing image as the source,
then retain an optimized copy on the first successful re-crop. Unknown legacy crop
metadata is not interpreted; the UI starts from a centered square.

DELETE clears avatar, retained source, and crop metadata. Repeating it is safe.
The frontend confirms removal, and Cancel leaves the photo untouched. Photo saves
and removal never change personal details, addresses, authentication state, or
recipients. The frontend keeps those form drafts mounted throughout photo changes.

## Storage lifecycle and errors

Photo changes serialize under the account lock. New files use unique names and
Django's configured default storage (local or Azure). Replacement, removal, and
account deletion schedule cleanup after commit, preserving files still referenced
by another user's photo field. Failed saves roll back database writes and attempt
to delete newly written files. Post-commit deletion follows the existing robust
callback behavior; storage outages may leave files requiring later cleanup.

400 responses contain field errors; crop serializer errors may be nested. Missing
or invalid authentication returns 401; unsupported methods return 405. Storage or
other unexpected failures remain server errors. The frontend reloads the current
photo after an uncertain save/removal rather than blindly replaying the request;
if reload fails, photo actions wait for a successful reload. Personal-detail drafts
remain unchanged. Stale-source errors also reload the current photo.

## Header synchronization

Login and refresh expose the cropped avatar as `user.profile_image`. Confirmed
photo reads, saves, removals and recovery reloads update that field in the
frontend's AuthContext for the same account. The header uses the bundled avatar
when the URL is null or fails to load. A failed recovery read retains the last
confirmed shared URL until a successful read; photo editor actions remain blocked.
The header does not make a separate photo request.
