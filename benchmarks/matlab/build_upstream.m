function build_upstream(upstream_dir, receipt_file)
%BUILD_UPSTREAM Compile the unmodified official CMG MEX sources in place.
arguments
    upstream_dir (1,:) char
    receipt_file (1,:) char
end
mex_dir = fullfile(upstream_dir, 'mex');
original = pwd;
cleanup = onCleanup(@() cd(original));
cd(mex_dir);
makeSolverMex;
mex('-largeArrayDims', 'mx_splitforest_.c');
assert(~isempty(dir(['mx_preconditioner_.' mexext])), 'mx_preconditioner_ was not produced');
assert(~isempty(dir(['mx_splitforest_.' mexext])), 'mx_splitforest_ was not produced');
configuration = mex.getCompilerConfigurations('C', 'Selected');
receipt = struct();
receipt.success = true;
receipt.matlab_release = version('-release');
receipt.matlab_version = version;
receipt.mex_extension = mexext;
if ~isempty(configuration)
    receipt.compiler_name = configuration.Name;
    receipt.compiler_version = configuration.Version;
    receipt.compiler_location = configuration.Location;
else
    receipt.compiler_name = 'unknown';
    receipt.compiler_version = 'unknown';
    receipt.compiler_location = 'unknown';
end
fid = fopen(receipt_file, 'w');
assert(fid >= 0, 'Could not write MEX receipt');
file_cleanup = onCleanup(@() fclose(fid));
fprintf(fid, '%s\n', jsonencode(receipt, PrettyPrint=true));
fprintf('CMG_MEX_BUILD_SUCCESS release=%s\n', version('-release'));
end
