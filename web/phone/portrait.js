/** A roomy 3:4 crop around one detected face, or null if the person is too close. */
export function portraitCrop(box, frameWidth, frameHeight) {
  if (!Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite)) return null;
  const [x, y, w, h] = box;
  if (w <= 0 || h <= 0 || frameWidth <= 0 || frameHeight <= 0) return null;

  const height = Math.min(Math.max(w * 2.4, h * 3), frameHeight, frameWidth / .75);
  const width = height * .75;
  const left = Math.max(0, Math.min(x + w / 2 - width / 2, frameWidth - width));
  const top = Math.max(0, Math.min(y + h * .65 - height / 2, frameHeight - height));
  if (w > width * .7 || h > height * .65 || x < left || y < top || x + w > left + width || y + h > top + height) return null;
  return {left, top, width, height};
}
