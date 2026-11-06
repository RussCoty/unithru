ardour {
	["type"] = "EditorHook",
	name = "UniThru Simulator Preview",
	author = "UniThru contributors",
	description = "Send one explicitly mapped audio track's monitoring state to the local UniThru preview. Does not change audio or mixer controls.",
}

function action_params ()
	return {
		["port"] = { title = "Preview listener port (127.0.0.1 only)", default = "0" },
		["track_id"] = { title = "Ardour track ID", default = "" },
		["input_port"] = { title = "Expected connected input port", default = "" },
	}
end

function signals ()
	return LuaSignal.Set():add ({ [LuaSignal.LuaTimerDS] = true })
end

function factory (params)
	local port = tonumber (params["port"])
	local track_id = params["track_id"] or ""
	local expected_input = params["input_port"] or ""
	assert (port and port == math.floor (port) and port > 0 and port <= 65535, "Configure the preview listener port")
	assert (#track_id > 0 and #expected_input > 0, "Configure the track ID and input connection")
	local tx = ARDOUR.LuaOSC.Address ("osc.udp://127.0.0.1:" .. tostring (port))
	local sequence = 0

	return function ()
		if not Session then return end
		sequence = sequence + 1
		local present, matches, monitor, armed = 0, 0, 0, 0
		local name = ""
		for route in Session:get_routes():iter() do
			if route:to_stateful():id():to_s() == track_id then
				local track = route:to_track()
				if not track:isnil() and not track:to_audio_track():isnil() then
					present = 1
					name = route:name()
					monitor = route:monitoring_state()
					armed = route:rec_enable_control():get_value() > 0 and 1 or 0
					if route:n_inputs():n_audio() == 1 then
						local input = route:input():audio(0)
						if not input:isnil() then
							local count, correct = 0, false
							local engine = Session:engine()
							local _, destinations = engine:get_connections(expected_input, C.StringVector())
							for destination in destinations[2]:iter() do
								local own_port = engine:get_port_by_name(destination)
								if not own_port:isnil() and own_port:name() == input:name() then
									local _, sources = engine:get_connections(destination, C.StringVector())
									for source in sources[2]:iter() do
										count = count + 1
										if source == expected_input then correct = true end
									end
									break
								end
							end
							matches = count == 1 and correct and 1 or 0
						end
					end
				end
				break
			end
		end
		tx:send ("/unithru/ardour-preview/v1/state", "ssssiiiiiii",
			Session:path(), track_id, expected_input, name, sequence,
			present, matches, monitor, armed,
			Session:transport_rolling() and 1 or 0,
			Session:actively_recording() and 1 or 0)
	end
end
