/** RDKB brand mark -- an original take on RDK Central's own visual
 * identity (rdkcentral.com): four stacked color bars (blue/yellow/green/
 * orange). Same color values as RDK's real logo, redrawn here as our own
 * simple rounded-bar glyph rather than embedding their actual logo file,
 * since this is our own app's icon, not a reproduction of their asset.
 */
export default function RdkMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="3.5" width="20" height="3.6" rx="1.8" fill="#00B0DA" />
      <rect x="2" y="8.8" width="20" height="3.6" rx="1.8" fill="#FCBB31" />
      <rect x="2" y="14.1" width="20" height="3.6" rx="1.8" fill="#94C73D" />
      <rect x="2" y="19.4" width="20" height="3.6" rx="1.8" fill="#F37D31" />
    </svg>
  )
}
