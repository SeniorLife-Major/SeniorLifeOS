import { afterEach, describe, expect, it, vi } from 'vitest'
import { checkPicked, FileProblem, prepareUpload, SAY, shrinkPhoto } from './prepareFile'

const MB = 1_000_000

function fileOf(size, name, type) {
  return new File([new Uint8Array(size)], name, { type })
}

async function problemOf(promise) {
  try {
    await promise
  } catch (error) {
    expect(error).toBeInstanceOf(FileProblem)
    return error.message
  }
  throw new Error('expected a FileProblem')
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('checkPicked', () => {
  it('lets through photos of any kind and PDFs up to 5 MB', () => {
    expect(checkPicked(fileOf(1000, 'card.jpg', 'image/jpeg'))).toBeNull()
    expect(checkPicked(fileOf(1000, 'card.png', 'image/png'))).toBeNull()
    expect(checkPicked(fileOf(1000, 'IMG_1.heic', 'image/heic'))).toBeNull() // redrawn as JPEG when sent
    expect(checkPicked(fileOf(8 * MB, 'big.jpg', 'image/jpeg'))).toBeNull() // made smaller when sent
    expect(checkPicked(fileOf(5 * MB, 'income.pdf', 'application/pdf'))).toBeNull()
  })

  it('says what is wrong with anything else', () => {
    expect(checkPicked(undefined)).toBe(SAY.notPhotoOrPdf)
    expect(checkPicked(fileOf(1000, 'notes.txt', 'text/plain'))).toBe(SAY.notPhotoOrPdf)
    expect(checkPicked(fileOf(1000, 'mystery', ''))).toBe(SAY.notPhotoOrPdf)
    expect(checkPicked(fileOf(0, 'card.jpg', 'image/jpeg'))).toBe(SAY.empty)
    expect(checkPicked(fileOf(5 * MB + 1, 'income.pdf', 'application/pdf'))).toBe(SAY.pdfTooBig)
  })
})

describe('prepareUpload', () => {
  it('sends a JPEG, PNG or PDF of up to 5 MB as it is', async () => {
    const shrink = vi.fn()
    for (const file of [
      fileOf(1000, 'card.jpg', 'image/jpeg'),
      fileOf(5 * MB, 'card.png', 'image/png'),
      fileOf(2 * MB, 'income.pdf', 'application/pdf'),
    ]) {
      expect(await prepareUpload(file, shrink)).toBe(file)
    }
    expect(shrink).not.toHaveBeenCalled()
  })

  it('makes a big photo, or one of another kind, into a smaller JPEG', async () => {
    const smaller = fileOf(900_000, 'photo.jpg', 'image/jpeg')
    const shrink = vi.fn(async () => smaller)

    const big = fileOf(5 * MB + 1, 'big.jpg', 'image/jpeg')
    expect(await prepareUpload(big, shrink)).toBe(smaller)
    const heic = fileOf(1000, 'IMG_1.heic', 'image/heic')
    expect(await prepareUpload(heic, shrink)).toBe(smaller)
    expect(shrink.mock.calls).toEqual([[big], [heic]])
  })

  it('says so when a photo cannot be made to fit, or a file cannot be used at all', async () => {
    const cannot = vi.fn(async () => null)

    expect(await problemOf(prepareUpload(fileOf(6 * MB, 'big.jpg', 'image/jpeg'), cannot))).toBe(SAY.photoTooBig)
    expect(await problemOf(prepareUpload(fileOf(1000, 'IMG_1.heic', 'image/heic'), cannot))).toBe(SAY.photoKind)
    expect(await problemOf(prepareUpload(fileOf(6 * MB, 'big.pdf', 'application/pdf'), cannot))).toBe(SAY.pdfTooBig)
    expect(await problemOf(prepareUpload(fileOf(1000, 'notes.txt', 'text/plain'), cannot))).toBe(SAY.notPhotoOrPdf)
    expect(cannot).toHaveBeenCalledTimes(2) // a PDF or a text file is never redrawn
  })
})

describe('shrinkPhoto', () => {
  it('draws the photo smaller, step by step, until the JPEG fits in 5 MB', async () => {
    const close = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 6000, height: 4000, close })))
    const drawn = []
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function () {
      return { drawImage: (image, x, y, width, height) => drawn.push([width, height]) }
    })
    // Pretend a JPEG takes 1.5 bytes per pixel: 3000 x 2000 (9 MB) and 2400 x 1600 (5.76 MB) are
    // too big, 2000 x 1333 (4 MB) fits.
    const bytes = (canvas) => Math.round(canvas.width * canvas.height * 1.5)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (done, type, quality) {
      done(new Blob([new Uint8Array(bytes(this))], { type }))
      drawn[drawn.length - 1].push(type, quality)
    })

    const result = await shrinkPhoto(fileOf(9 * MB, 'IMG_2041.HEIC', 'image/heic'))

    expect(drawn).toEqual([
      [3000, 2000, 'image/jpeg', 0.9],
      [2400, 1600, 'image/jpeg', 0.85],
      [2000, 1333, 'image/jpeg', 0.8],
    ])
    expect(result).toBeInstanceOf(File)
    expect(result.name).toBe('IMG_2041.jpg')
    expect(result.type).toBe('image/jpeg')
    expect(result.size).toBe(Math.round(2000 * 1333 * 1.5))
    expect(close).toHaveBeenCalled()
  })

  it('gives nothing when the browser cannot open the photo', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => Promise.reject(new DOMException('no', 'InvalidStateError'))))
    expect(await shrinkPhoto(fileOf(1000, 'IMG_1.heic', 'image/heic'))).toBeNull()

    vi.stubGlobal('createImageBitmap', undefined)
    expect(await shrinkPhoto(fileOf(1000, 'IMG_1.heic', 'image/heic'))).toBeNull()
  })
})
