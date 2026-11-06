emitted = {}
connections = { "system:capture_1" }
audio_channels = 1
monitor_state = 2
armed = false
track_present = true
is_audio = true

local function collection (items)
	return { iter = function ()
		local index = 0
		return function () index = index + 1; return items[index] end
	end }
end

function ardour (metadata) script_metadata = metadata end
LuaSignal = { LuaTimerDS = 1, Set = function ()
	return { add = function (self, selected) return selected end }
end }
ARDOUR = { LuaOSC = { Address = function (uri)
	emitted_uri = uri
	return { send = function (self, address, tags, ...)
		emitted = { address, tags, ... }
		if on_send then on_send (address, tags, ...) end
	end }
end } }
C = { StringVector = function () return {} end }

local input = {
	isnil = function () return false end,
	name = function () return "Vocal/audio_in 1" end,
}
local track = {
	isnil = function () return false end,
	to_audio_track = function () return { isnil = function () return not is_audio end } end,
}
local route = {
	to_stateful = function () return { id = function () return { to_s = function () return "track-7" end } end } end,
	to_track = function () return track end,
	name = function () return "Vocal" end,
	monitoring_state = function () return monitor_state end,
	rec_enable_control = function () return { get_value = function () return armed and 1 or 0 end } end,
	n_inputs = function () return { n_audio = function () return audio_channels end } end,
	input = function () return { audio = function () return input end } end,
}
Session = {
	get_routes = function () return collection (track_present and { route } or {}) end,
	path = function () return "/sessions/preview" end,
	transport_rolling = function () return false end,
	actively_recording = function () return false end,
	engine = function () return {
		get_port_by_name = function (self, name)
			if name == "ardour:Vocal/audio_in 1" then return input end
			return { isnil = function () return true end }
		end,
		get_connections = function (self, name)
			if name == "system:capture_1" then
				for _, source in ipairs (connections) do
					if source == name then return 1, { [2] = collection ({ "ardour:Vocal/audio_in 1" }) } end
				end
				return 0, { [2] = collection ({}) }
			end
			assert (name == "ardour:Vocal/audio_in 1", "Backend connection queries require a full port name")
			return #connections, { [2] = collection (connections) }
		end,
	} end,
}
