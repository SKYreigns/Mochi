import { useId } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { cn } from '@/lib/utils'

export type MascotState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'working'
  | 'success'
  | 'error'
  | 'sleeping'

export interface MascotProps {
  state?: MascotState
  size?: number
  className?: string
  title?: string
  animate?: boolean
}

const BODY_MOTION = {
  idle: { y: [0, -6, 0], rotate: [0, 0.6, 0], transition: { duration: 4.8, repeat: Infinity, ease: 'easeInOut' } },
  listening: {
    y: [0, -4, 0],
    rotate: [-1.5, 1.5, -1.5],
    transition: { duration: 3.2, repeat: Infinity, ease: 'easeInOut' },
  },
  thinking: {
    y: [0, -3, 0],
    rotate: [0, -2, 0],
    transition: { duration: 3.6, repeat: Infinity, ease: 'easeInOut' },
  },
  working: {
    y: [0, -5, 0],
    rotate: [-2.2, 2.2, -2.2],
    transition: { duration: 1.9, repeat: Infinity, ease: 'easeInOut' },
  },
  success: {
    y: [0, -10, 0],
    rotate: [-1, 1, -1],
    transition: { duration: 1.5, repeat: Infinity, ease: 'easeInOut' },
  },
  error: {
    y: [0, -2, 0],
    rotate: [-1.6, 0, -1.6],
    transition: { duration: 4, repeat: Infinity, ease: 'easeInOut' },
  },
  sleeping: { y: [0, -3, 0], rotate: 0, transition: { duration: 6, repeat: Infinity, ease: 'easeInOut' } },
} as const

function Sparkle({
  x,
  y,
  size,
  delay,
  color,
  reduced,
}: {
  x: number
  y: number
  size: number
  delay: number
  color: string
  reduced: boolean
}) {
  return (
    <path
      d={`M${x} ${y - size} C${x + size * 0.18} ${y - size * 0.2} ${x + size * 0.2} ${y - size * 0.18} ${x + size} ${y} C${x + size * 0.2} ${y + size * 0.18} ${x + size * 0.18} ${y + size * 0.2} ${x} ${y + size} C${x - size * 0.18} ${y + size * 0.2} ${x - size * 0.2} ${y + size * 0.18} ${x - size} ${y} C${x - size * 0.2} ${y - size * 0.18} ${x - size * 0.18} ${y - size * 0.2} ${x} ${y - size} Z`}
      fill={color}
      className={reduced ? undefined : 'animate-twinkle'}
      style={{ animationDelay: `${delay}s`, transformBox: 'fill-box', transformOrigin: 'center' }}
    />
  )
}

export function Mascot({
  state = 'idle',
  size = 140,
  className,
  title,
  animate = true,
}: MascotProps) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')
  const bodyGradient = `mochi-body-${uid}`
  const earGradient = `mochi-ear-${uid}`
  const glowGradient = `mochi-glow-${uid}`
  const reduced = Boolean(useReducedMotion()) || !animate
  const motionProps = reduced ? {} : BODY_MOTION[state]

  const sleeping = state === 'sleeping'
  const success = state === 'success'
  const error = state === 'error'
  const working = state === 'working'
  const listening = state === 'listening'
  const thinking = state === 'thinking'

  return (
    <motion.div
      className={cn('pointer-events-none relative select-none', className)}
      style={{ width: size, height: size }}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      animate={motionProps}
    >
      <svg viewBox="0 0 200 200" width="100%" height="100%" fill="none">
        <defs>
          <linearGradient id={bodyGradient} x1="52" y1="42" x2="150" y2="164" gradientUnits="userSpaceOnUse">
            <stop stopColor="#FFFFFF" />
            <stop offset="0.52" stopColor="#F3EDFF" />
            <stop offset="1" stopColor="#DCCEF8" />
          </linearGradient>
          <linearGradient id={earGradient} x1="60" y1="30" x2="90" y2="70" gradientUnits="userSpaceOnUse">
            <stop stopColor="#FFFFFF" />
            <stop offset="1" stopColor="#E6DBFA" />
          </linearGradient>
          <radialGradient id={glowGradient} cx="50%" cy="58%" r="50%">
            <stop stopColor="#C9F1F1" stopOpacity="0.72" />
            <stop offset="0.65" stopColor="#E8DEFF" stopOpacity="0.4" />
            <stop offset="1" stopColor="#E8DEFF" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* halo */}
        <ellipse cx="100" cy="118" rx="86" ry="76" fill={`url(#${glowGradient})`} />

        {/* floating decorations */}
        <Sparkle x={28} y={72} size={7} delay={0.4} color="#B9A1E8" reduced={reduced} />
        <Sparkle x={172} y={90} size={5.5} delay={1.6} color="#8CCFCF" reduced={reduced} />
        <Sparkle x={158} y={44} size={4.5} delay={2.4} color="#FFDCE9" reduced={reduced} />
        {success && (
          <>
            <Sparkle x={46} y={44} size={6} delay={0.2} color="#FFDCE9" reduced={reduced} />
            <Sparkle x={150} y={150} size={5} delay={0.9} color="#FFF1D9" reduced={reduced} />
          </>
        )}

        {/* shadow */}
        <ellipse cx="100" cy="178" rx="46" ry="8" fill="#B9A1E8" opacity="0.22" />

        <g transform={listening ? 'rotate(-2 100 110)' : success ? 'rotate(1.5 100 110)' : undefined}>
          {/* ears */}
          <g>
            <path
              d="M55 64C43 48 43 31 57 33C70 35 79 49 80 65C72 68 63 68 55 64Z"
              fill={`url(#${earGradient})`}
              stroke="#D8CAF2"
              strokeWidth="2"
            />
            <path d="M59 57C52 48 52 40 58 41C65 42 70 50 71 58C67 59 63 59 59 57Z" fill="#FFDCE9" opacity="0.9" />
            <path
              d="M145 64C157 48 157 31 143 33C130 35 121 49 120 65C128 68 137 68 145 64Z"
              fill={`url(#${earGradient})`}
              stroke="#D8CAF2"
              strokeWidth="2"
            />
            <path d="M141 57C148 48 148 40 142 41C135 42 130 50 129 58C133 59 137 59 141 57Z" fill="#FFDCE9" opacity="0.9" />
          </g>

          {/* tuft */}
          <path
            d="M96 45C92 31 99 21 110 17C115 28 111 39 104 47C101 48 98 47 96 45Z"
            fill="#F1E9FF"
            stroke="#D8CAF2"
            strokeWidth="2"
          />

          {/* body */}
          <path
            d="M100 44C129 44 155 62 162 93C169 124 154 155 125 166C111 171 89 171 75 166C46 155 31 124 38 93C45 62 71 44 100 44Z"
            fill={`url(#${bodyGradient})`}
            stroke="#D8CAF2"
            strokeWidth="2.4"
          />

          {/* belly highlight */}
          <ellipse cx="100" cy="140" rx="34" ry="20" fill="#FFFFFF" opacity="0.5" />

          {/* cheeks */}
          <ellipse cx="58" cy="121" rx="13" ry="8" fill="#FFDCE9" opacity="0.75" />
          <ellipse cx="142" cy="121" rx="13" ry="8" fill="#FFDCE9" opacity="0.75" />

          {/* eyes */}
          {sleeping || success ? (
            <g stroke="#4E4562" strokeWidth="4.5" strokeLinecap="round" fill="none">
              <path d="M68 104C73 97 82 97 87 104" />
              <path d="M113 104C118 97 127 97 132 104" />
            </g>
          ) : (
            <g>
              <g className={reduced || state !== 'idle' ? undefined : 'animate-blink'}>
                <ellipse
                  cx="77"
                  cy={listening ? 100 : thinking ? 99 : 101}
                  rx={listening ? 10.5 : 9.5}
                  ry={listening ? 12.5 : 11.5}
                  fill="#4E4562"
                />
                <circle
                  cx={listening ? 80.5 : 80}
                  cy={listening ? 95.5 : thinking ? 95 : 96.5}
                  r="3.1"
                  fill="#FFFFFF"
                />
                <circle cx={thinking ? 74 : 74} cy={thinking ? 104 : 105} r="1.5" fill="#FFFFFF" opacity="0.65" />
              </g>
              <g className={reduced || state !== 'idle' ? undefined : 'animate-blink'}>
                <ellipse
                  cx="123"
                  cy={listening ? 100 : thinking ? 99 : 101}
                  rx={listening ? 10.5 : 9.5}
                  ry={listening ? 12.5 : 11.5}
                  fill="#4E4562"
                />
                <circle
                  cx={listening ? 126.5 : 126}
                  cy={listening ? 95.5 : thinking ? 95 : 96.5}
                  r="3.1"
                  fill="#FFFFFF"
                />
                <circle cx={thinking ? 120 : 120} cy={thinking ? 104 : 105} r="1.5" fill="#FFFFFF" opacity="0.65" />
              </g>
            </g>
          )}

          {/* eyebrows */}
          {error && (
            <g stroke="#6E6285" strokeWidth="3" strokeLinecap="round">
              <path d="M68 87C73 84 79 84 84 87" />
              <path d="M116 87C121 84 127 84 132 87" />
            </g>
          )}
          {working && (
            <g stroke="#6E6285" strokeWidth="2.6" strokeLinecap="round" opacity="0.8">
              <path d="M69 88C74 86 80 86 85 88" />
              <path d="M115 88C120 86 126 86 131 88" />
            </g>
          )}

          {/* mouth */}
          {success ? (
            <path
              d="M88 121C93 130 107 130 112 121"
              stroke="#4E4562"
              strokeWidth="3.6"
              strokeLinecap="round"
              fill="#FFDCE9"
            />
          ) : error ? (
            <path d="M92 128C96 123 104 123 108 128" stroke="#4E4562" strokeWidth="3.4" strokeLinecap="round" />
          ) : listening ? (
            <ellipse cx="100" cy="124" rx="5.5" ry="6.5" fill="#4E4562" />
          ) : thinking ? (
            <path d="M93 125C97 123 103 123 107 125" stroke="#4E4562" strokeWidth="3.2" strokeLinecap="round" />
          ) : sleeping ? (
            <path d="M94 124C97 127 103 127 106 124" stroke="#4E4562" strokeWidth="3" strokeLinecap="round" />
          ) : (
            <path
              d="M92 121C95 126 99 126 100 123C101 126 105 126 108 121"
              stroke="#4E4562"
              strokeWidth="3.2"
              strokeLinecap="round"
              fill="none"
            />
          )}

          {/* little paws */}
          <ellipse cx="76" cy="158" rx="11" ry="7" fill="#FFFFFF" opacity="0.85" />
          <ellipse cx="124" cy="158" rx="11" ry="7" fill="#FFFFFF" opacity="0.85" />
        </g>

        {/* thinking dots */}
        {thinking && !reduced && (
          <motion.g
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1.6, repeat: Infinity }}
          >
            <circle cx="146" cy="38" r="3.5" fill="#B9A1E8" />
            <circle cx="158" cy="30" r="4.5" fill="#B9A1E8" opacity="0.8" />
            <circle cx="172" cy="22" r="5.5" fill="#B9A1E8" opacity="0.6" />
          </motion.g>
        )}

        {/* working orbit */}
        {working && !reduced && (
          <motion.g
            style={{ transformOrigin: '100px 110px' }}
            animate={{ rotate: 360 }}
            transition={{ duration: 7, repeat: Infinity, ease: 'linear' }}
          >
            <circle cx="100" cy="20" r="4" fill="#8CCFCF" />
            <circle cx="176" cy="108" r="3" fill="#FFDCE9" />
          </motion.g>
        )}

        {/* sleeping zzz */}
        {sleeping && (
          <g fill="#9B7FD9" fontFamily="Nunito, sans-serif" fontWeight="800">
            <text x="148" y="54" fontSize="16" opacity="0.85">
              z
            </text>
            <text x="160" y="38" fontSize="12" opacity="0.6">
              z
            </text>
            <text x="169" y="26" fontSize="9" opacity="0.4">
              z
            </text>
          </g>
        )}
      </svg>
    </motion.div>
  )
}

export function MascotAvatar({
  state = 'idle',
  size = 34,
  className,
}: {
  state?: MascotState
  size?: number
  className?: string
}) {
  return (
    <div
      className={cn(
        'grid shrink-0 place-items-center overflow-hidden rounded-full border border-line bg-gradient-to-br from-white/90 to-accent-soft',
        className,
      )}
      style={{ width: size, height: size }}
    >
      <Mascot state={state} size={size * 1.18} />
    </div>
  )
}
