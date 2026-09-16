import fedopsLogo from './assets/fedops-logo.png'

const FAVICON_SIZE = 64

function roundedSquare(
  context: CanvasRenderingContext2D,
  inset: number,
  size: number,
  radius: number,
) {
  const right = inset + size
  const bottom = inset + size

  context.beginPath()
  context.moveTo(inset + radius, inset)
  context.lineTo(right - radius, inset)
  context.quadraticCurveTo(right, inset, right, inset + radius)
  context.lineTo(right, bottom - radius)
  context.quadraticCurveTo(right, bottom, right - radius, bottom)
  context.lineTo(inset + radius, bottom)
  context.quadraticCurveTo(inset, bottom, inset, bottom - radius)
  context.lineTo(inset, inset + radius)
  context.quadraticCurveTo(inset, inset, inset + radius, inset)
  context.closePath()
}

export function installFedOpsFavicon() {
  const image = new Image()

  image.addEventListener('load', () => {
    const canvas = document.createElement('canvas')
    canvas.width = FAVICON_SIZE
    canvas.height = FAVICON_SIZE

    const context = canvas.getContext('2d')
    if (!context) return

    roundedSquare(context, 1, 62, 16)
    context.shadowColor = 'rgba(24, 24, 24, 0.12)'
    context.shadowBlur = 2
    context.shadowOffsetY = 1
    context.fillStyle = '#ffffff'
    context.fill()

    context.shadowColor = 'transparent'
    context.lineWidth = 1.5
    context.strokeStyle = 'rgba(24, 24, 24, 0.18)'
    context.stroke()
    context.drawImage(image, 4, 4, 56, 56)

    const link = document.querySelector<HTMLLinkElement>('link[rel~="icon"]')
      ?? document.head.appendChild(document.createElement('link'))
    link.rel = 'icon'
    link.type = 'image/png'
    link.sizes = `${FAVICON_SIZE}x${FAVICON_SIZE}`
    link.href = canvas.toDataURL('image/png')
  }, { once: true })

  image.src = fedopsLogo
}
