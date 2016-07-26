import drake
import os

class Packager(drake.Builder):

  """
  Only the name of the folder containing the licenses needs to be passed.
  The builder will automatically populate the list of source nodes by traversing
  the folder.
  """

  def __init__(self, license_folder, out_file):
    self.__license_folder = license_folder
    self.__context = drake.Drake.current.prefix
    walk_dir = os.path.normpath(
      str(drake.path_source() / self.__context / license_folder))
    licenses = list()
    for root, _, files in os.walk(walk_dir, followlinks = True):
      rel_loc = root.split(str(license_folder))[-1][1:]
      for f in files:
        if not f.startswith('.'):
          p = drake.Path('%s/%s/%s' % (license_folder, rel_loc, f))
          licenses.append(drake.node(p))
    self.__target = out_file
    super().__init__(licenses, [out_file])
    self.__sorted_sources = \
      list(map(lambda s: str(s), self.sources().values()))
    self.__sorted_sources.sort(key = lambda s: s.lower())

  def execute(self):
    print('Generating aggregated license file: %s' % self.__target)
    with open(str(self.__target), 'w') as out:
      for license in self.__sorted_sources:
        l_name = license.replace(
          '%s/%s/' % (self.__context, self.__license_folder), '')
        out.write('# Begin: %s\n(*%s\n' % (l_name, 78 * '-'))
        with open(str(drake.path_source() / license), 'r') as f:
          out.write(f.read())
        out.write('\n%s*)\n# End: %s\n\n' % (78 * '-', l_name))
    return True