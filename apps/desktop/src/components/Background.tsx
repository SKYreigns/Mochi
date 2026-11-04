import { useReducedMotion } from 'framer-motion'

function Star({ className, delay = 0 }: { className?: string; delay?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      style={{ animationDelay: `${delay}s` }}
      aria-hidden
    >
      <path
        d="M12 1.8c.6 5.6 4.6 9.6 10.2 10.2-5.6.6-9.6 4.6-10.2 10.2C11.4 16.6 7.4 12.6 1.8 12 7.4 11.4 11.4 7.4 12 1.8Z"
        fill="currentColor"
      />
    </svg>
  )
}

function Cloud({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 220 90" className={className} aria-hidden>
      <defs>
        <linearGradient id="cloud-fill" x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="white" stopOpacity="0.85" />
          <stop offset="1" stopColor="#E8DEFF" stopOpacity="0.5" />
        </linearGradient>
      </defs>
      <path
        d="M34 66c-14 0-24-9-24-21 0-13 11-22 24-21 4-14 16-23 31-23 17 0 30 11 33 27 4-2 8-3 12-3 16 0 28 12 28 27 0 8-3 14-9 19H34Z"
        fill="url(#cloud-fill)"
      />
    </svg>
  )
}

export function Background() {
  const reduced = useReducedMotion()

  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden>
      <div className="absolute inset-0 bg-[var(--bg)]" />

      {/* soft aurora wash */}
      <div className="absolute -inset-[20%] opacity-90 [background:radial-gradient(46%_46%_at_18%_12%,var(--blob-lilac)_0%,transparent_70%),radial-gradient(42%_42%_at_82%_18%,var(--blob-aqua)_0%,transparent_72%),radial-gradient(48%_48%_at_72%_88%,var(--blob-blush)_0%,transparent_70%),radial-gradient(38%_38%_at_28%_82%,var(--blob-cream)_0%,transparent_70%)]" />

      {/* organic blobs */}
      <div
        className={
          reduced
            ? 'absolute -left-24 top-24 size-[26rem] rounded-full bg-[var(--blob-lilac)] blur-3xl opacity-60'
            : 'animate-drift absolute -left-24 top-24 size-[26rem] rounded-full bg-[var(--blob-lilac)] opacity-60 blur-3xl'
        }
      />
      <div
        className={
          reduced
            ? 'absolute -right-20 top-1/3 size-[22rem] rounded-full bg-[var(--blob-aqua)] blur-3xl opacity-60'
            : 'animate-drift absolute -right-20 top-1/3 size-[22rem] rounded-full bg-[var(--blob-aqua)] opacity-60 blur-3xl'
        }
        style={{ animationDelay: '-7s', animationDuration: '24s' }}
      />
      <div
        className={
          reduced
            ? 'absolute bottom-[-6rem] left-1/3 size-[24rem] rounded-full bg-[var(--blob-blush)] blur-3xl opacity-50'
            : 'animate-drift absolute bottom-[-6rem] left-1/3 size-[24rem] rounded-full bg-[var(--blob-blush)] opacity-50 blur-3xl'
        }
        style={{ animationDelay: '-12s', animationDuration: '28s' }}
      />

      {/* clouds */}
      <Cloud className="absolute left-[6%] top-[12%] w-40 opacity-50" />
      <Cloud className="absolute right-[8%] top-[7%] w-32 opacity-35" />
      <Cloud className="absolute bottom-[16%] right-[16%] w-44 opacity-30" />

      {/* stars */}
      <Star className="absolute left-[14%] top-[26%] w-3 text-lilac opacity-70 animate-twinkle" delay={0.5} />
      <Star className="absolute left-[46%] top-[9%] w-2.5 text-seafoam opacity-60 animate-twinkle" delay={1.8} />
      <Star className="absolute right-[18%] top-[30%] w-3.5 text-blush opacity-80 animate-twinkle" delay={1.1} />
      <Star className="absolute right-[34%] bottom-[14%] w-2 text-lilac opacity-60 animate-twinkle" delay={2.6} />
      <Star className="absolute left-[30%] bottom-[8%] w-2.5 text-seafoam opacity-50 animate-twinkle" delay={0.2} />

      {/* subtle grain */}
      <div className="absolute inset-0 opacity-[0.18] [background-image:radial-gradient(rgba(85,76,105,0.18)_0.5px,transparent_0.5px)] [background-size:22px_22px]" />
    </div>
  )
}
