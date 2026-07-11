/**
 * ForceGraph — a self-contained "deep-space neon" force-directed graph
 * renderer on HTML5 Canvas. No external dependencies.
 *
 *   - velocity-Verlet force simulation (repulsion + springs + gravity)
 *   - radial-gradient node halos with breathing pulse (additive blending)
 *   - gradient bezier edges; hovered edges get a travelling light pulse
 *   - drifting starfield backdrop + subtle vignette
 *   - pan / zoom / node-drag / hover-highlight interaction
 *
 * Rendering happens in CSS pixels; the base transform accounts for devicePixelRatio.
 */

import { colorOf, iconOf } from './graphTheme'

export interface RawEntity {
  id: string
  name?: string
  type?: string
  description?: string
}

export interface RawRelationship {
  source_id: string
  target_id: string
  type?: string
  description?: string
}

interface GNode {
  id: string
  name: string
  type: string
  description: string
  degree: number
  color: string
  icon: string
  x: number
  y: number
  vx: number
  vy: number
  r: number
  phase: number
  fixed: boolean
}

interface GEdge {
  s: GNode
  t: GNode
  type: string
}

export interface HoverPayload {
  node: GNode | null
  /** mouse position in CSS pixels relative to the canvas */
  x: number
  y: number
}

export interface ForceGraphOptions {
  onHover?: (p: HoverPayload) => void
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

export class ForceGraph {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private opts: ForceGraphOptions

  private nodes: GNode[] = []
  private edges: GEdge[] = []
  private byId = new Map<string, GNode>()
  private neighbors = new Map<string, Set<string>>()

  // camera (CSS px)
  private scale = 1
  private offsetX = 0
  private offsetY = 0

  // simulation
  private alpha = 1
  private time = 0
  private raf = 0
  private running = false

  // interaction
  private hoverNode: GNode | null = null
  private dragNode: GNode | null = null
  private dragMoved = false
  private panning = false
  private last = { x: 0, y: 0 }

  // display
  private dpr = 1
  private W = 0
  private H = 0
  private stars: { x: number; y: number; r: number; a: number; tw: number }[] = []

  constructor(canvas: HTMLCanvasElement, opts: ForceGraphOptions = {}) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d')!
    this.opts = opts
    this.bindEvents()
  }

  // ── public API ──────────────────────────────────────────────

  setData(entities: RawEntity[], relationships: RawRelationship[]) {
    const ids = new Set(entities.map((e) => e.id))
    const rels = relationships.filter((r) => ids.has(r.source_id) && ids.has(r.target_id))

    // degree
    const degree = new Map<string, number>()
    const neighbors = new Map<string, Set<string>>()
    const addN = (a: string, b: string) => {
      if (!neighbors.has(a)) neighbors.set(a, new Set())
      neighbors.get(a)!.add(b)
    }
    for (const r of rels) {
      degree.set(r.source_id, (degree.get(r.source_id) || 0) + 1)
      degree.set(r.target_id, (degree.get(r.target_id) || 0) + 1)
      addN(r.source_id, r.target_id)
      addN(r.target_id, r.source_id)
    }
    this.neighbors = neighbors

    // preserve positions of nodes that already exist so re-filtering doesn't jump
    const prev = this.byId
    const cx = this.W / 2 || 400
    const cy = this.H / 2 || 300
    const GOLDEN = Math.PI * (3 - Math.sqrt(5))

    this.nodes = entities.map((e, i) => {
      const type = e.type || 'Entity'
      const deg = degree.get(e.id) || 0
      const old = prev.get(e.id)
      // seed new nodes on a golden-angle spiral to avoid a central singularity
      const rad = 40 + Math.sqrt(i + 1) * 34
      const ang = i * GOLDEN
      return {
        id: e.id,
        name: (e.name || e.id).slice(0, 28),
        type,
        description: e.description || '',
        degree: deg,
        color: colorOf(type),
        icon: iconOf(type),
        x: old ? old.x : cx + Math.cos(ang) * rad,
        y: old ? old.y : cy + Math.sin(ang) * rad,
        vx: 0,
        vy: 0,
        r: 6 + Math.min(Math.sqrt(deg) * 4, 26),
        phase: (i % 20) * 0.31,
        fixed: false,
      }
    })

    this.byId = new Map(this.nodes.map((n) => [n.id, n]))
    this.edges = rels.map((r) => ({
      s: this.byId.get(r.source_id)!,
      t: this.byId.get(r.target_id)!,
      type: r.type || '',
    }))

    // warm up the layout synchronously so first paint is already settled
    this.alpha = 1
    if (this.W && this.H) {
      for (let i = 0; i < 140; i++) this.tick()
      this.fitView()
    }
  }

  start() {
    if (this.running) return
    this.running = true
    const loop = () => {
      if (!this.running) return
      this.time += 0.016
      if (this.alpha > 0.008 && !this.dragNode) this.tick()
      else if (this.dragNode) this.tick()
      this.render()
      this.raf = requestAnimationFrame(loop)
    }
    this.raf = requestAnimationFrame(loop)
  }

  stop() {
    this.running = false
    cancelAnimationFrame(this.raf)
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect()
    this.dpr = window.devicePixelRatio || 1
    this.W = rect.width
    this.H = rect.height
    this.canvas.width = Math.round(rect.width * this.dpr)
    this.canvas.height = Math.round(rect.height * this.dpr)
    this.buildStars()
    if (!this.scaleInitialized && this.nodes.length) {
      this.fitView()
      this.scaleInitialized = true
    }
  }
  private scaleInitialized = false

  fitView() {
    if (!this.nodes.length || !this.W) return
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const n of this.nodes) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x)
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y)
    }
    const pad = 80
    const gw = Math.max(maxX - minX, 1)
    const gh = Math.max(maxY - minY, 1)
    this.scale = clamp(Math.min((this.W - pad) / gw, (this.H - pad) / gh), 0.15, 2.2)
    this.offsetX = this.W / 2 - ((minX + maxX) / 2) * this.scale
    this.offsetY = this.H / 2 - ((minY + maxY) / 2) * this.scale
  }

  /** Zoom by a factor around the center of the viewport */
  zoom(factor: number) {
    const cx = this.W / 2
    const cy = this.H / 2
    const worldX = (cx - this.offsetX) / this.scale
    const worldY = (cy - this.offsetY) / this.scale
    this.scale = clamp(this.scale * factor, 0.1, 3.5)
    this.offsetX = cx - worldX * this.scale
    this.offsetY = cy - worldY * this.scale
  }

  destroy() {
    this.stop()
    this.unbindEvents()
  }

  // ── simulation ──────────────────────────────────────────────

  private tick() {
    const nodes = this.nodes
    const n = nodes.length
    if (!n) return
    const a = this.alpha
    const cx = this.W / 2
    const cy = this.H / 2

    // repulsion (O(n^2) — fine up to a few hundred nodes)
    for (let i = 0; i < n; i++) {
      const p = nodes[i]
      for (let j = i + 1; j < n; j++) {
        const q = nodes[j]
        let dx = p.x - q.x
        let dy = p.y - q.y
        let d2 = dx * dx + dy * dy
        if (d2 < 0.01) { dx = (i - j) * 0.5 + 0.1; dy = 0.3; d2 = dx * dx + dy * dy }
        const minD = (p.r + q.r + 14)
        const charge = d2 < minD * minD ? 5200 : 2600 // extra push when overlapping
        const f = (charge * a) / d2
        const d = Math.sqrt(d2)
        const fx = (dx / d) * f
        const fy = (dy / d) * f
        p.vx += fx; p.vy += fy
        q.vx -= fx; q.vy -= fy
      }
    }

    // spring attraction along edges
    for (const e of this.edges) {
      const dx = e.t.x - e.s.x
      const dy = e.t.y - e.s.y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const L = 70 + (e.s.r + e.t.r)
      const f = ((d - L) * 0.015 * a)
      const fx = (dx / d) * f
      const fy = (dy / d) * f
      e.s.vx += fx; e.s.vy += fy
      e.t.vx -= fx; e.t.vy -= fy
    }

    // gravity to center + integrate
    for (const p of nodes) {
      p.vx += (cx - p.x) * 0.0016 * a
      p.vy += (cy - p.y) * 0.0016 * a
      if (p === this.dragNode) { p.vx = 0; p.vy = 0; continue }
      p.vx *= 0.82
      p.vy *= 0.82
      // clamp velocity to avoid explosions
      p.vx = clamp(p.vx, -40, 40)
      p.vy = clamp(p.vy, -40, 40)
      p.x += p.vx
      p.y += p.vy
    }

    this.alpha *= 0.985
  }

  // ── rendering ───────────────────────────────────────────────

  private render() {
    const ctx = this.ctx
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
    const W = this.W, H = this.H

    // backdrop
    const bg = ctx.createLinearGradient(0, 0, W * 0.4, H)
    bg.addColorStop(0, '#0A0E1A')
    bg.addColorStop(0.55, '#0E1424')
    bg.addColorStop(1, '#131B30')
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, W, H)
    // radial glow from center
    const gl = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, Math.max(W, H) * 0.7)
    gl.addColorStop(0, 'rgba(45,225,194,0.06)')
    gl.addColorStop(1, 'rgba(10,14,26,0)')
    ctx.fillStyle = gl
    ctx.fillRect(0, 0, W, H)

    this.drawStars(ctx)

    const hi = this.hoverNode
    const near = hi ? this.neighbors.get(hi.id) : null
    const isLit = (nd: GNode) => !hi || nd === hi || (near ? near.has(nd.id) : false)

    // ── edges (additive glow) ──
    ctx.globalCompositeOperation = 'lighter'
    for (const e of this.edges) {
      const lit = !hi || (isLit(e.s) && isLit(e.t) && (e.s === hi || e.t === hi))
      if (hi && !lit) continue
      this.drawEdge(ctx, e, hi ? 1 : 0.5)
    }
    // dimmed edges when hovering
    if (hi) {
      ctx.globalCompositeOperation = 'source-over'
      for (const e of this.edges) {
        const lit = isLit(e.s) && isLit(e.t) && (e.s === hi || e.t === hi)
        if (lit) continue
        this.drawEdge(ctx, e, 0.06)
      }
      ctx.globalCompositeOperation = 'lighter'
    }

    // ── node halos ──
    for (const nd of this.nodes) {
      const lit = isLit(nd)
      this.drawHalo(ctx, nd, lit ? 1 : 0.12)
    }
    ctx.globalCompositeOperation = 'source-over'

    // ── node cores + labels ──
    for (const nd of this.nodes) {
      const lit = isLit(nd)
      this.drawNode(ctx, nd, lit ? 1 : 0.18)
    }
  }

  private drawEdge(ctx: CanvasRenderingContext2D, e: GEdge, alpha: number) {
    const s = this.toScreen(e.s.x, e.s.y)
    const t = this.toScreen(e.t.x, e.t.y)
    // gentle arc: control point offset perpendicular to the segment
    const mx = (s.x + t.x) / 2
    const my = (s.y + t.y) / 2
    const dx = t.x - s.x
    const dy = t.y - s.y
    const len = Math.hypot(dx, dy) || 1
    const bend = clamp(len * 0.12, 0, 46)
    const cxp = mx - (dy / len) * bend
    const cyp = my + (dx / len) * bend

    const grad = ctx.createLinearGradient(s.x, s.y, t.x, t.y)
    grad.addColorStop(0, this.rgba(e.s.color, alpha * 0.85))
    grad.addColorStop(1, this.rgba(e.t.color, alpha * 0.85))

    ctx.beginPath()
    ctx.moveTo(s.x, s.y)
    ctx.quadraticCurveTo(cxp, cyp, t.x, t.y)
    ctx.strokeStyle = grad
    ctx.lineWidth = Math.max(0.6, 1.2 * this.scale)
    ctx.stroke()

    // travelling light pulse on hovered edges
    const hovered = this.hoverNode && (e.s === this.hoverNode || e.t === this.hoverNode)
    if (hovered) {
      const prog = (this.time * 0.6) % 1
      // sample point along the quadratic bezier
      const u = 1 - prog
      const px = u * u * s.x + 2 * u * prog * cxp + prog * prog * t.x
      const py = u * u * s.y + 2 * u * prog * cyp + prog * prog * t.y
      const dot = ctx.createRadialGradient(px, py, 0, px, py, 9)
      dot.addColorStop(0, this.rgba(e.t.color, 0.95))
      dot.addColorStop(1, this.rgba(e.t.color, 0))
      ctx.fillStyle = dot
      ctx.beginPath()
      ctx.arc(px, py, 9, 0, Math.PI * 2)
      ctx.fill()
    }
  }

  private drawHalo(ctx: CanvasRenderingContext2D, nd: GNode, alpha: number) {
    const p = this.toScreen(nd.x, nd.y)
    const pulse = 1 + Math.sin(this.time * 1.6 + nd.phase) * 0.14
    const R = (nd.r + 10) * this.scale * pulse * (nd === this.hoverNode ? 1.5 : 1)
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, R)
    g.addColorStop(0, this.rgba(nd.color, 0.55 * alpha))
    g.addColorStop(0.5, this.rgba(nd.color, 0.18 * alpha))
    g.addColorStop(1, this.rgba(nd.color, 0))
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(p.x, p.y, R, 0, Math.PI * 2)
    ctx.fill()
  }

  private drawNode(ctx: CanvasRenderingContext2D, nd: GNode, alpha: number) {
    const p = this.toScreen(nd.x, nd.y)
    const r = nd.r * this.scale
    // core with a soft top-light gradient
    const core = ctx.createRadialGradient(p.x - r * 0.35, p.y - r * 0.35, r * 0.1, p.x, p.y, r)
    core.addColorStop(0, this.mix(nd.color, '#ffffff', 0.55, alpha))
    core.addColorStop(0.6, this.rgba(nd.color, alpha))
    core.addColorStop(1, this.mix(nd.color, '#000000', 0.35, alpha))
    ctx.fillStyle = core
    ctx.beginPath()
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2)
    ctx.fill()

    // bright rim
    ctx.strokeStyle = this.mix(nd.color, '#ffffff', 0.5, alpha)
    ctx.lineWidth = 1.2
    ctx.stroke()

    // icon for larger / hovered nodes
    if (r >= 11) {
      ctx.globalAlpha = alpha
      ctx.font = `${Math.round(r * 1.1)}px system-ui, "Apple Color Emoji", sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(nd.icon, p.x, p.y + r * 0.05)
      ctx.globalAlpha = 1
    }

    // label
    const showLabel = nd.r >= 10 || this.scale > 0.75 || nd === this.hoverNode
    if (showLabel && alpha > 0.3) {
      const fs = clamp(11 * Math.max(this.scale, 0.85), 10, 15)
      ctx.font = `600 ${fs}px Inter, system-ui, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      const ly = p.y + r + 4
      ctx.shadowColor = 'rgba(0,0,0,0.85)'
      ctx.shadowBlur = 4
      ctx.fillStyle = nd === this.hoverNode
        ? this.mix(nd.color, '#ffffff', 0.6, 1)
        : `rgba(226,232,240,${alpha})`
      ctx.fillText(nd.name, p.x, ly)
      ctx.shadowBlur = 0
    }
  }

  private drawStars(ctx: CanvasRenderingContext2D) {
    ctx.globalCompositeOperation = 'lighter'
    for (const st of this.stars) {
      const tw = 0.5 + 0.5 * Math.sin(this.time * st.tw + st.x)
      ctx.fillStyle = `rgba(180,210,255,${st.a * tw})`
      ctx.beginPath()
      ctx.arc(st.x, st.y, st.r, 0, Math.PI * 2)
      ctx.fill()
    }
    ctx.globalCompositeOperation = 'source-over'
  }

  private buildStars() {
    const count = Math.round((this.W * this.H) / 9000)
    this.stars = Array.from({ length: count }, () => ({
      x: Math.random() * this.W,
      y: Math.random() * this.H,
      r: Math.random() * 1.3 + 0.2,
      a: Math.random() * 0.5 + 0.15,
      tw: Math.random() * 2 + 0.5,
    }))
  }

  // ── helpers ─────────────────────────────────────────────────

  private toScreen(x: number, y: number) {
    return { x: x * this.scale + this.offsetX, y: y * this.scale + this.offsetY }
  }
  private toWorld(x: number, y: number) {
    return { x: (x - this.offsetX) / this.scale, y: (y - this.offsetY) / this.scale }
  }

  private rgba(hex: string, a: number) {
    const { r, g, b } = this.hexRgb(hex)
    return `rgba(${r},${g},${b},${a})`
  }
  private mix(hex: string, other: string, t: number, a: number) {
    const c1 = this.hexRgb(hex)
    const c2 = this.hexRgb(other)
    const r = Math.round(c1.r + (c2.r - c1.r) * t)
    const g = Math.round(c1.g + (c2.g - c1.g) * t)
    const b = Math.round(c1.b + (c2.b - c1.b) * t)
    return `rgba(${r},${g},${b},${a})`
  }
  private hexRgb(hex: string) {
    const h = hex.replace('#', '')
    const v = h.length === 3
      ? h.split('').map((c) => c + c).join('')
      : h
    const n = parseInt(v, 16)
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
  }

  private pick(sx: number, sy: number): GNode | null {
    const w = this.toWorld(sx, sy)
    let best: GNode | null = null
    let bestD = Infinity
    for (const nd of this.nodes) {
      const dx = nd.x - w.x
      const dy = nd.y - w.y
      const d = dx * dx + dy * dy
      const hit = (nd.r + 6) * (nd.r + 6)
      if (d < hit && d < bestD) { best = nd; bestD = d }
    }
    return best
  }

  // ── events ──────────────────────────────────────────────────

  private onDown = (e: MouseEvent) => {
    const { x, y } = this.localPos(e)
    const hit = this.pick(x, y)
    this.last = { x, y }
    this.dragMoved = false
    if (hit) {
      this.dragNode = hit
      hit.fixed = true
      this.alpha = Math.max(this.alpha, 0.4)
    } else {
      this.panning = true
    }
  }

  private onMove = (e: MouseEvent) => {
    const { x, y } = this.localPos(e)
    if (this.dragNode) {
      const w = this.toWorld(x, y)
      this.dragNode.x = w.x
      this.dragNode.y = w.y
      this.dragMoved = true
      return
    }
    if (this.panning) {
      this.offsetX += x - this.last.x
      this.offsetY += y - this.last.y
      this.last = { x, y }
      return
    }
    const hit = this.pick(x, y)
    if (hit !== this.hoverNode) {
      this.hoverNode = hit
      this.canvas.style.cursor = hit ? 'pointer' : 'grab'
    }
    this.opts.onHover?.({ node: this.hoverNode, x, y })
  }

  private onUp = () => {
    if (this.dragNode) this.dragNode.fixed = false
    this.dragNode = null
    this.panning = false
  }

  private onLeave = () => {
    this.hoverNode = null
    this.panning = false
    this.opts.onHover?.({ node: null, x: 0, y: 0 })
  }

  private onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const { x, y } = this.localPos(e)
    const factor = Math.exp(-e.deltaY * 0.0015)
    const ns = clamp(this.scale * factor, 0.15, 4)
    this.offsetX = x - (x - this.offsetX) * (ns / this.scale)
    this.offsetY = y - (y - this.offsetY) * (ns / this.scale)
    this.scale = ns
  }

  private localPos(e: MouseEvent | WheelEvent) {
    const rect = this.canvas.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  private bindEvents() {
    this.canvas.addEventListener('mousedown', this.onDown)
    window.addEventListener('mousemove', this.onMove)
    window.addEventListener('mouseup', this.onUp)
    this.canvas.addEventListener('mouseleave', this.onLeave)
    this.canvas.addEventListener('wheel', this.onWheel, { passive: false })
  }
  private unbindEvents() {
    this.canvas.removeEventListener('mousedown', this.onDown)
    window.removeEventListener('mousemove', this.onMove)
    window.removeEventListener('mouseup', this.onUp)
    this.canvas.removeEventListener('mouseleave', this.onLeave)
    this.canvas.removeEventListener('wheel', this.onWheel)
  }
}
