const paths = {
  cube: <><path d="m12 3 9 5v8l-9 5-9-5V8Z" /><path d="m3 8 9 5 9-5M12 13v8M7.5 5.5l9 5" /></>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></>,
  arrow: <><path d="M4 12h16m-6-6 6 6-6 6" /></>,
  play: <path d="m8 5 11 7-11 7Z" />,
  sliders: <><path d="M4 7h6m4 0h6M4 17h10m4 0h2" /><circle cx="12" cy="7" r="2" /><circle cx="16" cy="17" r="2" /></>,
  results: <><path d="M5 3h14v18H5zM8 8h8M8 12h5M8 16h3" /></>,
  github: <><path d="M9 20c-5 1.5-5-2.5-7-3m14 6v-4a3.5 3.5 0 0 0-1-3c3-.3 6-1.5 6-6a4.7 4.7 0 0 0-1.3-3.3A4.3 4.3 0 0 0 19.6 3S18.4 2.6 16 4a11.5 11.5 0 0 0-6 0C7.6 2.6 6.4 3 6.4 3a4.3 4.3 0 0 0-.1 3.7A4.7 4.7 0 0 0 5 10c0 4.5 3 5.7 6 6a3.5 3.5 0 0 0-1 3v4" /></>,
  external: <><path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
};

export default function Icon({ name, size = 20, ...props }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {paths[name]}
    </svg>
  );
}
