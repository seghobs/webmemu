import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {title: 'ACCUNIQ Studio · Yerel Dashboard', description: 'BC380 ölçümleri için yerel analiz ve kayıt paneli'};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="tr"><body>{children}</body></html>;
}
