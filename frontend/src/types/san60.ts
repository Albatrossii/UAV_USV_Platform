// The supplied Python engine is authoritative; transport coordinates remain metres.
export interface San60Track {
  p: number[]; dbm: number; time: number; latitude?: number; longitude?: number; altitude_m?: number
}
export interface San60Space {
  track: San60Track[]; cells: number[][]; field: number[][]; span: number[]; voxel_size: number
  hotspot?: { p: number[]; dbm: number } | null
  source?: { p: number[]; rmse_db: number; stability_m: number; label?: string } | null
  reason?: string
}
export interface San60State {
  mode: string; message: string; frames: number; rate_hz: number; arrival_age_s: number | null
  unpaired: number; invalid_frames: number; rejected_jumps: number; band: number[]
  coordinate_unit: string; display_unit: string
  gnss: { valid: boolean; latitude: number; longitude: number; altitude_m: number; satellites_used?: number; positioning_snr_avg?: number } | null
  origin: number[] | null
  latest: { time: number; sequence?: number; source?: string; frequencies: number[]; powers: number[]; bins: number; peak_dbm: number; peak_mhz: number; band_dbm: number; temperature_c?: number } | null
  spatial: San60Space | null
  logs: { time: number; text: string }[]
  capabilities: { usb: boolean; usb_reason: string; default_bag: string }
}
export interface San60Status { connected: boolean; message: string; serviceUrl: string; state: San60State | null }
