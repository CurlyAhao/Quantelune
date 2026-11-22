# StatForge: fixed, argument-only filtered pitch measurement.
form: "StatForge filtered pitch"
    sentence: "Audio", ""
    sentence: "Output pitch", ""
    boolean: "Cross correlation", "no"
    positive: "Time step", "0.01"
    positive: "Pitch floor", "75"
    positive: "Pitch top", "600"
    natural: "Maximum candidates", "15"
    boolean: "Very accurate", "no"
    positive: "Attenuation", "0.03"
    real: "Silence threshold", "0.09"
    real: "Voicing threshold", "0.50"
    real: "Octave cost", "0.055"
    real: "Octave jump cost", "0.35"
    real: "Voiced unvoiced cost", "0.14"
endform
Read from file: audio$
if cross_correlation
    To Pitch (filtered cross-correlation): time_step, pitch_floor, pitch_top, maximum_candidates, very_accurate, attenuation, silence_threshold, voicing_threshold, octave_cost, octave_jump_cost, voiced_unvoiced_cost
else
    To Pitch (filtered autocorrelation): time_step, pitch_floor, pitch_top, maximum_candidates, very_accurate, attenuation, silence_threshold, voicing_threshold, octave_cost, octave_jump_cost, voiced_unvoiced_cost
endif
Save as short text file: output_pitch$
