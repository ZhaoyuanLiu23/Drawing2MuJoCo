import "./globals.css";

export const metadata = {
  title: "Drawing2MuJoCo | Engineering Workbench",
  description: "From Engineering Drawing to Robot Simulation. Local Web V0.1 interface preview.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
