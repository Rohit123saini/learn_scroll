// Single source of truth for the post-media frame ratio limits, shared by the
// real frame (PostMediaUtil.frameHeight), the feed skeleton and the new-post
// ratio picker, so a skeleton, the loaded card and the composer can never
// disagree about how tall a post may be.
//
// Instagram-style limits: nothing taller than 4:5 portrait, nothing wider than
// 1.91:1 landscape. Anything outside is letterboxed (blur + contain), never cropped.

/// Tallest allowed frame: 4:5 portrait (width / height).
const double kPostMediaMinRatio = 4 / 5;

/// Widest allowed frame: 1.91:1 landscape.
const double kPostMediaMaxRatio = 1.91;

/// Ratio used when the backend has no width/height yet (old rows) and for the
/// feed skeleton, so the placeholder is the same size as the common case.
const double kPostMediaDefaultRatio = 1.0;
